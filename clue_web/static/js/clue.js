/*
  Seat-specific browser runtime for the standalone Clue table.

  Maintainer notes:
  - This file owns the polling loop, DOM rendering, seat-local draft state, and
    action/notebook/chat submission UX for one joined seat.
  - Server responses are authoritative; the browser only preserves in-progress
    human input such as notebook text, chat text, and action dropdown choices.
  - Public/private visibility boundaries are enforced server-side, so the UI
    should treat returned snapshots as already filtered for the current seat.
*/
const app = document.getElementById("game-app");

if (app) {
  const seatToken = app.dataset.seatToken;
  const narrativeLog = document.getElementById("narrative-log");
  const chatLog = document.getElementById("chat-log");
  const privateLog = document.getElementById("private-log");
  const seatList = document.getElementById("seat-list");
  const handList = document.getElementById("hand-list");
  const seatSummary = document.getElementById("seat-summary");
  const board = document.getElementById("board");
  const positionGrid = document.getElementById("position-grid");
  const actionControls = document.getElementById("action-controls");
  const actionPanel = document.getElementById("action-panel");
  const turnBanner = document.getElementById("turn-banner");
  const phasePill = document.getElementById("phase-pill");
  const activeSeatLabel = document.getElementById("active-seat-label");
  const turnIndexLabel = document.getElementById("turn-index-label");
  const legalCount = document.getElementById("legal-count");
  const notebookText = document.getElementById("notebook-text");
  const chatInput = document.getElementById("chat-input");
  const saveNotebook = document.getElementById("save-notebook");
  const sendChat = document.getElementById("send-chat");
  const gameTitle = document.getElementById("game-title");
  const actionStatus = document.getElementById("action-status");
  const decisionContextCard = document.getElementById("decision-context-card");
  const decisionContext = document.getElementById("decision-context");
  const turnGuidance = document.getElementById("turn-guidance");
  const legalSummary = document.getElementById("legal-summary");
  const paceNote = document.getElementById("pace-note");
  const requestError = document.getElementById("request-error");
  const notebookStatus = document.getElementById("notebook-status");
  const seatStatePill = document.getElementById("seat-state-pill");
  const narrativeCount = document.getElementById("narrative-count");
  const chatCount = document.getElementById("chat-count");
  const chatFeedCount = document.getElementById("chat-feed-count");
  const privateCount = document.getElementById("private-count");
  const seatDebug = document.getElementById("seat-debug");
  const debugStatus = document.getElementById("debug-status");
  const aiExplainer = document.getElementById("ai-explainer");

  const CHARACTER_COLORS = {
    "Miss Scarlet": "#c43c4d",
    "Colonel Mustard": "#c8a63a",
    "Mrs. White": "#ece5d6",
    "Mr. Green": "#4f8e52",
    "Mrs. Peacock": "#3f78b2",
    "Professor Plum": "#7b5fa8",
  };
  const ROOM_COLORS = {
    study: "#e0d6f2",
    hall: "#efe1c5",
    lounge: "#f3cfc2",
    library: "#d6e6cf",
    billiard: "#cfe1d7",
    dining: "#f0dbc0",
    conservatory: "#d7ecd7",
    ballroom: "#e7d8ef",
    kitchen: "#f5e0bd",
  };
  const ACTION_LABELS = {
    roll: "Roll",
    move: "Move",
    suggest: "Suggest",
    show_refute_card: "Show Refute Card",
    pass_refute: "Pass Refute",
    accuse: "Accuse",
    end_turn: "End Turn",
    send_chat: "Send Chat",
  };
  const POLL_FAST_MS = 900;
  const POLL_NORMAL_MS = 2200;
  const POLL_IDLE_MS = 4200;
  const clockFormatter = new Intl.DateTimeFormat(undefined, {
    hour: "numeric",
    minute: "2-digit",
  });

  let currentSnapshot = null;
  let snapshotKey = "";
  let notebookDirty = false;
  let chatDirty = false;
  let refreshTimer = null;
  let refreshing = false;
  let eventCursor = 0;
  let legalFingerprint = "";
  let pendingMutation = "";
  let requestSequence = 0;
  let lastAppliedSequence = 0;
  let chatUnread = 0;
  let forceChatScroll = false;
  // Draft state is kept outside the latest snapshot so polling can redraw from
  // server-authoritative data without clobbering in-progress human input.
  const actionDrafts = new Map();
  const seenEventIndices = new Set();
  const eventsByChannel = {
    narrative: [],
    chat: [],
    private: [],
  };

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function displaySeatKind(seatKind) {
    const normalized = String(seatKind ?? "").trim().toLowerCase();
    if (normalized === "heuristic" || normalized === "llm") {
      return "LLM";
    }
    if (normalized === "human") {
      return "Human";
    }
    if (normalized === "np") {
      return "NP";
    }
    return normalized || "Unknown";
  }

  function titleize(value) {
    return String(value ?? "")
      .replaceAll("_", " ")
      .replace(/\b\w/g, (match) => match.toUpperCase());
  }

  function seatMap(snapshot) {
    const map = new Map();
    snapshot.seats.forEach((seat) => {
      map.set(seat.seat_id, {
        ...seat,
        color: CHARACTER_COLORS[seat.character] || "#8e2331",
      });
    });
    return map;
  }

  function seatName(snapshot, seatId) {
    return seatMap(snapshot).get(seatId)?.display_name || seatId || "Unknown";
  }

  function actionLabel(action) {
    return ACTION_LABELS[action] || titleize(action);
  }

  function deskActions(snapshot) {
    return (snapshot.legal_actions?.available || []).filter((action) => action !== "send_chat");
  }

  function currentViewState(snapshot) {
    if (!snapshot) {
      return "loading";
    }
    if (snapshot.status === "complete") {
      return "complete";
    }
    if (currentSeatIsRefuting(snapshot)) {
      return "refute";
    }
    if (snapshot.active_seat_id === snapshot.seat.seat_id) {
      return "your-turn";
    }
    const activeSeat = seatMap(snapshot).get(snapshot.active_seat_id);
    return activeSeat && activeSeat.seat_kind !== "human" ? "ai-turn" : "waiting";
  }

  function setState(node, state) {
    if (node) {
      node.dataset.state = state;
    }
  }

  function boardLabelById(snapshot) {
    const map = new Map();
    snapshot.board_nodes.forEach((node) => {
      map.set(node.id, node.label);
    });
    return map;
  }

  function boardNodeById(snapshot) {
    const map = new Map();
    snapshot.board_nodes.forEach((node) => {
      map.set(node.id, node);
    });
    return map;
  }

  function setMoveDraft(nodeId) {
    const select = document.getElementById("move-target");
    if (!select) {
      return;
    }
    select.value = nodeId;
    actionDrafts.set("move-target", nodeId);
  }

  function currentSeatIsRefuting(snapshot) {
    const available = new Set(snapshot.legal_actions?.available || []);
    return available.has("show_refute_card") || available.has("pass_refute");
  }

  function waitingOnAutonomousSeat(snapshot) {
    if (!snapshot || snapshot.status !== "active") {
      return false;
    }
    if (snapshot.active_seat_id === snapshot.seat.seat_id || currentSeatIsRefuting(snapshot)) {
      return false;
    }
    const activeSeat = seatMap(snapshot).get(snapshot.active_seat_id);
    return Boolean(activeSeat && activeSeat.seat_kind !== "human");
  }

  function nextRefreshDelay(snapshot) {
    if (!snapshot) {
      return POLL_NORMAL_MS;
    }
    if (snapshot.status !== "active") {
      return POLL_IDLE_MS;
    }
    return waitingOnAutonomousSeat(snapshot) ? POLL_FAST_MS : POLL_NORMAL_MS;
  }

  function scheduleRefresh(next = currentSnapshot) {
    if (refreshTimer) {
      window.clearTimeout(refreshTimer);
    }
    const delay = typeof next === "number" ? next : nextRefreshDelay(next);
    // Poll more aggressively only while waiting on an autonomous seat so the UI
    // feels responsive without making human-only tables constantly hammer the API.
    refreshTimer = window.setTimeout(() => {
      refresh();
    }, delay);
  }

  function showError(message) {
    requestError.classList.remove("hidden");
    requestError.textContent = message;
  }

  function clearError() {
    requestError.classList.add("hidden");
    requestError.textContent = "";
  }

  async function request(path, options = {}) {
    // Every authenticated game request is scoped by the signed seat token.
    const response = await fetch(path, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        "X-Clue-Seat-Token": seatToken,
        ...(options.headers || {}),
      },
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.error || `Request failed (${response.status}).`);
    }
    clearError();
    return payload;
  }

  function prettifyPhase(phase) {
    return titleize(phase);
  }

  function nearBottom(container, threshold = 42) {
    return (container.scrollHeight - (container.scrollTop + container.clientHeight)) <= threshold;
  }

  function formatClock(value) {
    if (!value) {
      return "";
    }
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return "";
    }
    return clockFormatter.format(date);
  }

  function renderBoard(snapshot) {
    const highlights = new Set((snapshot.legal_actions.move_targets || []).map((item) => item.node_id));
    const seatsById = seatMap(snapshot);
    const nodesById = boardNodeById(snapshot);
    const seatPositions = {};
    const surface = document.createElementNS("http://www.w3.org/2000/svg", "g");
    surface.setAttribute("transform", "translate(58 42) scale(0.78)");

    snapshot.seats.forEach((seat) => {
      if (!seatPositions[seat.position]) {
        seatPositions[seat.position] = [];
      }
      seatPositions[seat.position].push(seatsById.get(seat.seat_id));
    });

    board.innerHTML = "";
    (snapshot.board_edges || []).forEach((edge) => {
      const from = nodesById.get(edge.from);
      const to = nodesById.get(edge.to);
      if (!from || !to) {
        return;
      }
      const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
      line.setAttribute("class", `board-edge board-edge-${edge.kind || "walk"}`);
      line.setAttribute("x1", from.x);
      line.setAttribute("y1", from.y);
      line.setAttribute("x2", to.x);
      line.setAttribute("y2", to.y);
      surface.appendChild(line);
    });
    snapshot.board_nodes.forEach((node) => {
      const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
      const nodeClasses = [`node`, node.kind];
      if (highlights.has(node.id)) {
        nodeClasses.push("highlight");
      }
      if (snapshot.seat.position === node.id) {
        nodeClasses.push("current-seat-node");
      }
      g.setAttribute("class", nodeClasses.join(" "));
      if (node.kind === "room") {
        const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
        rect.setAttribute("x", node.x - 65);
        rect.setAttribute("y", node.y - 38);
        rect.setAttribute("width", 130);
        rect.setAttribute("height", 76);
        rect.setAttribute("rx", 14);
        rect.setAttribute("fill", ROOM_COLORS[node.id] || "#f6edd9");
        g.appendChild(rect);
      } else {
        const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
        circle.setAttribute("cx", node.x);
        circle.setAttribute("cy", node.y);
        circle.setAttribute("r", node.kind === "hallway" ? 16 : 12);
        if (node.kind === "start") {
          const seatColor = CHARACTER_COLORS[Object.keys(CHARACTER_COLORS).find((name) => node.label.includes(name.split(" ").slice(-1)[0]))] || "#efe3c7";
          circle.setAttribute("fill", seatColor);
          circle.setAttribute("fill-opacity", "0.32");
        }
        g.appendChild(circle);
      }

      const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
      label.setAttribute("x", node.x);
      label.setAttribute("y", node.y + (node.kind === "room" ? 4 : 34));
      label.setAttribute("text-anchor", "middle");
      label.setAttribute("font-size", node.kind === "room" ? "15" : "11");
      label.textContent = node.label;
      g.appendChild(label);

      const tokens = seatPositions[node.id] || [];
      tokens.forEach((seat, index) => {
        const token = document.createElementNS("http://www.w3.org/2000/svg", "circle");
        token.setAttribute("class", "token");
        token.setAttribute("cx", node.x - 28 + (index * 18));
        token.setAttribute("cy", node.y - (node.kind === "room" ? 18 : 0));
        token.setAttribute("r", 8);
        token.setAttribute("fill", seat.color);
        g.appendChild(token);
      });
      if (highlights.has(node.id)) {
        g.classList.add("clickable-node");
        g.addEventListener("click", () => {
          setMoveDraft(node.id);
        });
      }
      surface.appendChild(g);
    });
    board.appendChild(surface);
  }

  function renderPositionGrid(snapshot) {
    const labels = boardLabelById(snapshot);
    const seatsById = seatMap(snapshot);
    positionGrid.innerHTML = snapshot.seats.map((seat) => {
      const decorated = seatsById.get(seat.seat_id);
      const classes = ["position-card"];
      if (seat.seat_id === snapshot.active_seat_id) {
        classes.push("is-active");
      }
      if (seat.seat_id === snapshot.seat.seat_id) {
        classes.push("is-you");
      }
      if (!seat.can_win) {
        classes.push("is-out");
      }
      return `
        <article class="${classes.join(" ")}">
          <p class="card-kicker">${escapeHtml(displaySeatKind(seat.seat_kind))}</p>
          <h4>${escapeHtml(seat.display_name)}</h4>
          <p>${seat.display_name === seat.character ? "Character marker" : escapeHtml(seat.character)}</p>
          <p class="position-node">${escapeHtml(labels.get(seat.position) || seat.position)}</p>
          <p>${seat.can_win ? "Still in the case." : "Eliminated from winning."}</p>
          <span class="seat-swatch" style="--seat-color: ${escapeHtml(decorated.color)}"></span>
        </article>
      `;
    }).join("");
  }

  function renderSeatSummary(snapshot) {
    const labels = boardLabelById(snapshot);
    const activeName = seatName(snapshot, snapshot.active_seat_id);
    const tableState = snapshot.status === "complete"
      ? "Case closed"
      : snapshot.active_seat_id === snapshot.seat.seat_id
        ? "Acting now"
        : `Waiting on ${activeName}`;
    seatStatePill.textContent = snapshot.seat.can_win ? "Live Case" : "Out";
    seatStatePill.dataset.state = snapshot.seat.can_win ? "live" : "out";
    seatSummary.innerHTML = `
      <div class="seat-summary-grid">
        <article class="summary-stat">
          <span class="card-kicker">Character</span>
          <strong>${escapeHtml(snapshot.seat.character)}</strong>
        </article>
        <article class="summary-stat">
          <span class="card-kicker">Marker</span>
          <strong>${escapeHtml(labels.get(snapshot.seat.position) || snapshot.seat.position)}</strong>
        </article>
        <article class="summary-stat">
          <span class="card-kicker">Seat Type</span>
          <strong>${escapeHtml(displaySeatKind(snapshot.seat.seat_kind))}</strong>
        </article>
        <article class="summary-stat">
          <span class="card-kicker">Status</span>
          <strong>${snapshot.seat.can_win ? "Live case" : "Out of contention"}</strong>
        </article>
        <article class="summary-stat">
          <span class="card-kicker">Hand Size</span>
          <strong>${escapeHtml(snapshot.seat.hand_count)}</strong>
        </article>
        <article class="summary-stat">
          <span class="card-kicker">Table Pulse</span>
          <strong>${escapeHtml(tableState)}</strong>
        </article>
        <article class="summary-stat">
          <span class="card-kicker">Phase</span>
          <strong>${escapeHtml(prettifyPhase(snapshot.phase))}</strong>
        </article>
      </div>
    `;
  }

  function renderSeatCards(snapshot) {
    const labels = boardLabelById(snapshot);
    const seatsById = seatMap(snapshot);
    seatList.innerHTML = snapshot.seats.map((seat) => {
      const decorated = seatsById.get(seat.seat_id);
      const classes = ["seat-card"];
      if (seat.seat_id === snapshot.active_seat_id) {
        classes.push("is-active");
      }
      if (seat.seat_id === snapshot.seat.seat_id) {
        classes.push("is-you");
      }
      if (!seat.can_win) {
        classes.push("is-out");
      }
      return `
        <article class="${classes.join(" ")}">
          <div class="seat-card-head">
            <span class="seat-swatch" style="--seat-color: ${escapeHtml(decorated.color)}"></span>
            <div>
              <h3>${escapeHtml(seat.display_name)}</h3>
              <p>${seat.display_name === seat.character ? escapeHtml(displaySeatKind(seat.seat_kind)) : escapeHtml(seat.character)}</p>
            </div>
          </div>
          <p>${escapeHtml(labels.get(seat.position) || seat.position)}</p>
          <p>${escapeHtml(displaySeatKind(seat.seat_kind))} · ${seat.can_win ? "alive" : "eliminated"}</p>
        </article>
      `;
    }).join("");
  }

  function isTraceEvent(event) {
    return String(event?.event_type || "").startsWith("trace_");
  }

  function eventChannel(event) {
    if (isTraceEvent(event)) return "ignore";
    if (event.visibility === "public" && event.event_type === "chat_posted") return "chat";
    if (event.visibility === "public") return "narrative";
    return "private";
  }

  function describeEventType(event, channel) {
    if (channel === "chat") {
      return "Table Talk";
    }
    const overrides = {
      game_created: "Table Opened",
      turn_started: "Turn Start",
      suggestion_made: "Suggestion",
      accusation_made: "Accusation",
      accusation_wrong: "Wrong Accusation",
      accusation_correct: "Solved Case",
      refute_passed: "Refute Pass",
      private_card_shown: "Card Revealed",
      suspect_moved: "Marker Moved",
      dice_rolled: "Roll Result",
    };
    return overrides[event.event_type] || titleize(event.event_type || "");
  }

  function eventActorName(snapshot, event) {
    const seatId = event?.payload?.seat_id;
    return seatId ? seatName(snapshot, seatId) : "";
  }

  function renderEventItem(snapshot, event) {
    const channel = eventChannel(event);
    const actorName = eventActorName(snapshot, event);
    const timestamp = formatClock(event.created_at);
    const label = describeEventType(event, channel);
    const message = channel === "chat" && event.payload?.text
      ? event.payload.text
      : event.message;
    const li = document.createElement("li");
    li.className = `log-item ${event.visibility === "public" ? "is-public" : "is-private"} is-${channel}`;
    li.dataset.eventIndex = String(event.event_index || "");
    li.dataset.channel = channel;
    li.innerHTML = `
      <div class="log-meta">
        <span class="log-badge">${escapeHtml(channel === "chat" ? "Chat" : event.visibility === "public" ? "Public" : "Private")}</span>
        <span class="log-type">${escapeHtml(label)}</span>
        ${actorName ? `<span class="log-stamp">${escapeHtml(actorName)}</span>` : ""}
        ${timestamp ? `<span class="log-time">${escapeHtml(timestamp)}</span>` : ""}
      </div>
      <div class="log-body">
        ${channel === "chat" ? `<p class="log-speaker">${escapeHtml(actorName || "Table")}</p>` : ""}
        <p class="log-message">${escapeHtml(message)}</p>
      </div>
    `;
    return li;
  }

  function appendEventItems(container, snapshot, events, { chatChannel = false } = {}) {
    if (!events.length) return;
    const shouldStick = forceChatScroll || nearBottom(container);
    const emptyState = container.querySelector(".empty-state");
    if (emptyState) {
      emptyState.remove();
    }
    const fragment = document.createDocumentFragment();
    events.forEach((event) => {
      fragment.appendChild(renderEventItem(snapshot, event));
    });
    container.appendChild(fragment);
    if (chatChannel && !shouldStick) {
      chatUnread += events.length;
    }
    if (shouldStick) {
      container.scrollTop = container.scrollHeight;
      if (chatChannel) {
        chatUnread = 0;
      }
    }
  }

  function ensureEmptyState(container, items, emptyMessage) {
    if (items.length) return;
    container.innerHTML = `<li class="empty-state">${escapeHtml(emptyMessage)}</li>`;
  }

  function ingestEvents(events) {
    const appended = { narrative: [], chat: [], private: [] };
    [...events].sort((left, right) => Number(left.event_index || 0) - Number(right.event_index || 0)).forEach((event) => {
      const index = Number(event.event_index || 0);
      if (!Number.isFinite(index) || index <= 0 || seenEventIndices.has(index)) {
        return;
      }
      seenEventIndices.add(index);
      eventCursor = Math.max(eventCursor, index);
      const channel = eventChannel(event);
      if (channel === "ignore") {
        return;
      }
      eventsByChannel[channel].push(event);
      appended[channel].push(event);
    });
    return appended;
  }

  function renderEventPanels(snapshot, appended) {
    appendEventItems(narrativeLog, snapshot, appended.narrative);
    appendEventItems(chatLog, snapshot, appended.chat, { chatChannel: true });
    appendEventItems(privateLog, snapshot, appended.private);

    ensureEmptyState(narrativeLog, eventsByChannel.narrative, "The public story of the game will appear here.");
    ensureEmptyState(chatLog, eventsByChannel.chat, "The table chat stream will appear here.");
    ensureEmptyState(privateLog, eventsByChannel.private, "No private reveals or seat-only prompts yet.");

    narrativeCount.textContent = String(eventsByChannel.narrative.length);
    const totalChat = eventsByChannel.chat.length;
    const chatLabel = chatUnread > 0 ? `${totalChat} (+${chatUnread})` : String(totalChat);
    chatCount.textContent = chatLabel;
    chatFeedCount.textContent = chatLabel;
    privateCount.textContent = String(eventsByChannel.private.length);
    narrativeCount.dataset.state = eventsByChannel.narrative.length ? "active" : "calm";
    chatCount.dataset.state = chatUnread > 0 ? "attention" : "calm";
    chatFeedCount.dataset.state = chatUnread > 0 ? "attention" : "calm";
    privateCount.dataset.state = eventsByChannel.private.length ? "private" : "calm";
  }

  function legalSummaryText(snapshot) {
    const legal = snapshot.legal_actions || {};
    const actions = deskActions(snapshot);
    if (snapshot.status === "complete") {
      return "The case is closed. Review the wire and private briefing to reconstruct the finish.";
    }
    if (currentSeatIsRefuting(snapshot)) {
      const cardCount = (legal.refute_cards || []).length;
      return `${cardCount} eligible ${cardCount === 1 ? "card is" : "cards are"} available for a seat-private refute response.`;
    }
    if (snapshot.active_seat_id !== snapshot.seat.seat_id) {
      return actions.length
        ? `Off-turn private options remain available: ${actions.map(actionLabel).join(", ")}.`
        : "No private table action is required from this seat right now.";
    }
    if (actions.includes("move")) {
      const targetCount = (legal.move_targets || []).length;
      return `${targetCount} legal ${targetCount === 1 ? "destination is" : "destinations are"} highlighted on the board and mirrored below.`;
    }
    if (actions.includes("suggest")) {
      return "Build a room suggestion from the staged suspect and weapon fields, then commit it once satisfied.";
    }
    if (actions.includes("accuse") && actions.length === 1) {
      return "Accusation is live. Commit only when your private evidence is decisive.";
    }
    if (actions.includes("roll")) {
      return "Open the turn with a roll. Movement and follow-up decisions will unlock immediately after it resolves.";
    }
    if (actions.includes("end_turn") && actions.length === 1) {
      return "No further forced decisions are pending. End the turn when you are ready.";
    }
    return actions.length
      ? `Available now: ${actions.map(actionLabel).join(", ")}.`
      : "No private table action is required from this seat right now.";
  }

  function renderGuidance(snapshot) {
    const available = new Set(snapshot.legal_actions?.available || []);
    const activeSeat = seatMap(snapshot).get(snapshot.active_seat_id);
    const activeName = seatName(snapshot, snapshot.active_seat_id);
    const state = currentViewState(snapshot);
    const winnerName = snapshot.winner_seat_id ? seatName(snapshot, snapshot.winner_seat_id) : "Unknown";

    setState(turnBanner, state);
    setState(phasePill, state);
    setState(actionStatus, state);
    setState(decisionContextCard, state);
    setState(actionPanel, state);
    activeSeatLabel.textContent = snapshot.status === "complete" ? winnerName : activeName;
    turnIndexLabel.textContent = `Turn ${Number(snapshot.turn_index || 0) + 1}`;
    legalCount.textContent = snapshot.status === "complete" ? "Closed" : String(deskActions(snapshot).length);
    legalSummary.textContent = legalSummaryText(snapshot);

    if (snapshot.status === "complete") {
      actionStatus.textContent = "Case Closed";
      decisionContext.textContent = `${winnerName} closed the case.`;
      turnBanner.textContent = `Case closed. Winner: ${winnerName}`;
      turnGuidance.textContent = snapshot.winner_seat_id
        ? `${winnerName} won the game. You can keep reviewing the table record and private notes.`
        : "The case is closed.";
      return;
    }
    if (available.has("show_refute_card") || available.has("pass_refute")) {
      actionStatus.textContent = "Private Refute";
      decisionContext.textContent = "A seat-private response is waiting on you.";
      turnBanner.textContent = `${snapshot.seat.display_name}, resolve the private refute.`;
      turnGuidance.textContent = "You are being asked to refute a suggestion. Any shown card stays private to the suggesting seat.";
      return;
    }
    if (snapshot.active_seat_id === snapshot.seat.seat_id) {
      actionStatus.textContent = "Your Turn";
      decisionContext.textContent = `Turn ${Number(snapshot.turn_index || 0) + 1} is yours to resolve.`;
      turnBanner.textContent = `${snapshot.seat.display_name}, you are up.`;
      if (available.has("roll")) {
        turnGuidance.textContent = "Open the turn with a roll, or use a secret passage if one is available.";
      } else if (available.has("move")) {
        turnGuidance.textContent = "Choose a legal destination from the board highlights or the action controls.";
      } else if (available.has("suggest")) {
        turnGuidance.textContent = "You can suggest from your current room. Refutations will stay private when required.";
      } else if (available.has("accuse")) {
        turnGuidance.textContent = "Accusations end the question immediately. Use them only when your private evidence is strong.";
      } else {
        turnGuidance.textContent = "Your seat is up. Review the board state and finish the turn when ready.";
      }
      return;
    }
    if (activeSeat && activeSeat.seat_kind !== "human") {
      actionStatus.textContent = "AI Seat Acting";
      decisionContext.textContent = `${activeName} is resolving an autonomous turn.`;
      turnBanner.textContent = `${activeName} is resolving an autonomous turn.`;
      turnGuidance.textContent = `Waiting on ${activeName}. Auto-refresh is running at a faster cadence until the autonomous turn settles.`;
      return;
    }
    actionStatus.textContent = "Waiting";
    decisionContext.textContent = `${activeName} currently controls the table.`;
    turnBanner.textContent = `Waiting on ${activeName}.`;
    turnGuidance.textContent = activeSeat
      ? `Waiting on ${activeName} to act.`
      : "Waiting on the next seat.";
  }

  function renderSeatDebug(snapshot) {
    const debug = snapshot.analysis?.seat_debug || {};
    const metric = debug.metric || null;
    const toolSnapshot = debug.tool_snapshot || {};
    const topHypotheses = toolSnapshot.top_hypotheses || [];
    const topSuggestions = toolSnapshot.suggestion_ranking || [];
    const accusation = toolSnapshot.accusation || {};

    if (!metric && !topHypotheses.length && !topSuggestions.length) {
      debugStatus.textContent = "Idle";
      seatDebug.innerHTML = '<p class="empty-state">No private agent-debug payload has been recorded for this seat yet.</p>';
      return;
    }

    debugStatus.textContent = metric?.fallback_used ? "Fallback" : "Live";
    seatDebug.innerHTML = `
      <div class="debug-grid">
        <article class="summary-stat">
          <span class="card-kicker">Joint Entropy</span>
          <strong>${escapeHtml(toolSnapshot.belief_summary?.joint_case_entropy_bits ?? "--")}</strong>
        </article>
        <article class="summary-stat">
          <span class="card-kicker">Accuse Confidence</span>
          <strong>${escapeHtml(accusation.confidence ?? "--")}</strong>
        </article>
        <article class="summary-stat">
          <span class="card-kicker">Last Action</span>
          <strong>${escapeHtml(debug.decision?.action || metric?.action || "--")}</strong>
        </article>
        <article class="summary-stat">
          <span class="card-kicker">Latency</span>
          <strong>${escapeHtml(metric?.latency_ms ?? "--")} ms</strong>
        </article>
      </div>
      <div class="debug-block">
        <p class="card-kicker">Top Hypotheses</p>
        <ul class="debug-list">
          ${topHypotheses.length ? topHypotheses.map((item) => `<li>${escapeHtml(`${item.suspect} / ${item.weapon} / ${item.room} (${item.p})`)}</li>`).join("") : '<li>No hypothesis sample yet.</li>'}
        </ul>
      </div>
      <div class="debug-block">
        <p class="card-kicker">Top Suggestion</p>
        <p>${escapeHtml(topSuggestions[0]?.why || debug.decision_debug?.model_rationale || "No suggestion ranking yet.")}</p>
      </div>
    `;
  }

  function renderAiExplainer(snapshot) {
    const metrics = snapshot.analysis?.game_metrics || {};
    const targets = snapshot.analysis?.latency_targets_ms || {};
    aiExplainer.innerHTML = `
      <p class="field-note">LLM seats get a private deduction snapshot, choose one legal action with structured output, and fall back to the deterministic heuristic policy if the model times out, emits malformed JSON, or proposes an illegal move.</p>
      <div class="debug-grid">
        <article class="summary-stat">
          <span class="card-kicker">Fallback Rate</span>
          <strong>${escapeHtml(metrics.fallback_rate ?? 0)}</strong>
        </article>
        <article class="summary-stat">
          <span class="card-kicker">Guardrail Blocks</span>
          <strong>${escapeHtml(metrics.guardrail_blocks ?? 0)}</strong>
        </article>
        <article class="summary-stat">
          <span class="card-kicker">Tool Budget</span>
          <strong>${escapeHtml(targets.tool_snapshot_ms ?? "--")} ms</strong>
        </article>
        <article class="summary-stat">
          <span class="card-kicker">LLM Budget</span>
          <strong>${escapeHtml(targets.llm_turn_ms ?? "--")} ms</strong>
        </article>
      </div>
    `;
  }

  function updateDraftControls() {
    const busy = Boolean(pendingMutation);
    notebookStatus.textContent = notebookDirty ? "Unsaved" : "Synced";
    notebookStatus.dataset.state = notebookDirty ? "dirty" : "synced";
    saveNotebook.disabled = !notebookDirty || busy;
    sendChat.disabled = !chatInput.value.trim() || busy;
    saveNotebook.textContent = pendingMutation === "notebook" ? "Saving..." : "Save Notes";
    sendChat.textContent = pendingMutation === "chat" ? "Posting..." : "Send Chat";
    actionControls.querySelectorAll("button[data-action-button='1'], select").forEach((control) => {
      control.disabled = busy;
    });
  }

  function renderSummary(snapshot) {
    const maxEventIndex = Math.max(eventCursor, ...((snapshot.events || []).map((event) => Number(event.event_index || 0))));
    const nextSnapshotKey = [
      snapshot.status,
      snapshot.turn_index,
      snapshot.phase,
      snapshot.active_seat_id,
      maxEventIndex,
    ].join(":");
    if (nextSnapshotKey === snapshotKey && !(snapshot.events || []).length) {
      return;
    }

    currentSnapshot = snapshot;
    snapshotKey = nextSnapshotKey;
    gameTitle.textContent = snapshot.title;
    phasePill.textContent = prettifyPhase(snapshot.phase);
    paceNote.textContent = waitingOnAutonomousSeat(snapshot)
      ? "Faster polling is active while an autonomous seat resolves its turn."
      : "Steady polling keeps the table current without interrupting in-progress choices.";

    renderSeatSummary(snapshot);
    handList.innerHTML = snapshot.seat.hand.map((card) => `<li>${escapeHtml(card)}</li>`).join("");
    if (!notebookDirty) {
      notebookText.value = snapshot.notebook?.text || "";
    }

    renderSeatCards(snapshot);
    renderBoard(snapshot);
    renderPositionGrid(snapshot);

    const appended = ingestEvents(snapshot.events || []);
    renderEventPanels(snapshot, appended);

    const nextLegalFingerprint = JSON.stringify(snapshot.legal_actions || {});
    if (nextLegalFingerprint !== legalFingerprint) {
      legalFingerprint = nextLegalFingerprint;
      renderActions(snapshot);
    }
    renderGuidance(snapshot);
    renderSeatDebug(snapshot);
    renderAiExplainer(snapshot);
    updateDraftControls();
    forceChatScroll = false;
  }

  function buildSelect(id, options, labelText, valueField = "value", textField = "label") {
    // Draft selections survive polling so humans can think before clicking.
    const previousValue = actionDrafts.get(id) || "";
    const wrapper = document.createElement("label");
    wrapper.className = "action-row";
    wrapper.innerHTML = `<span>${escapeHtml(labelText)}</span>`;
    const select = document.createElement("select");
    select.id = id;
    options.forEach((option) => {
      const item = document.createElement("option");
      item.value = option[valueField];
      item.textContent = option[textField];
      select.appendChild(item);
    });
    if (previousValue && options.some((option) => option[valueField] === previousValue)) {
      select.value = previousValue;
    }
    actionDrafts.set(id, select.value);
    select.addEventListener("change", () => {
      actionDrafts.set(id, select.value);
    });
    wrapper.appendChild(select);
    return wrapper;
  }

  function createActionCard({ eyebrow, title, detail, tone = "neutral" }) {
    const card = document.createElement("section");
    card.className = "action-card";
    card.dataset.tone = tone;

    const head = document.createElement("div");
    head.className = "action-card-head";
    head.innerHTML = `
      <p class="card-kicker">${escapeHtml(eyebrow)}</p>
      <h4>${escapeHtml(title)}</h4>
      <p class="field-note action-card-detail">${escapeHtml(detail)}</p>
    `;

    const body = document.createElement("div");
    body.className = "action-card-body";
    card.appendChild(head);
    card.appendChild(body);
    return { card, body };
  }

  function addActionButton(container, text, payloadBuilder, extraClass = "") {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.actionButton = "1";
    button.textContent = text;
    if (extraClass) {
      button.className = extraClass;
    }
    button.addEventListener("click", async () => {
      await submitMutation("action", "api/v1/games/current/actions", payloadBuilder());
    });
    container.appendChild(button);
  }

  function renderActions(snapshot) {
    // Action controls are rebuilt every refresh because legality changes by phase.
    const legal = snapshot.legal_actions || {};
    const available = new Set(legal.available || []);
    actionControls.innerHTML = "";

    if (available.has("roll")) {
      const { card, body } = createActionCard({
        eyebrow: "Open Turn",
        title: "Roll For Movement",
        detail: "Start the turn. Follow-up movement stays staged until you confirm it.",
        tone: "primary",
      });
      addActionButton(body, "Roll", () => ({ action: "roll" }));
      actionControls.appendChild(card);
    }
    if (available.has("move") && (legal.move_targets || []).length) {
      const targetCount = legal.move_targets.length;
      const options = legal.move_targets.map((item) => ({ value: item.node_id, label: `${item.label} (${item.cost})` }));
      const { card, body } = createActionCard({
        eyebrow: "Movement",
        title: "Stage A Destination",
        detail: `${targetCount} legal ${targetCount === 1 ? "destination is" : "destinations are"} available. Board clicks and the dropdown stay in sync.`,
        tone: "primary",
      });
      body.appendChild(buildSelect("move-target", options, "Move To"));
      addActionButton(body, "Move", () => ({
        action: "move",
        target_node: document.getElementById("move-target").value,
      }));
      actionControls.appendChild(card);
    }
    if (available.has("suggest")) {
      const suspects = snapshot.case_file_categories.suspect.map((item) => ({ value: item, label: item }));
      const weapons = snapshot.case_file_categories.weapon.map((item) => ({ value: item, label: item }));
      const { card, body } = createActionCard({
        eyebrow: "Case Theory",
        title: "Make A Suggestion",
        detail: "Suggestions stay tied to your current room and can trigger private refutation flow.",
        tone: "neutral",
      });
      body.appendChild(buildSelect("suggest-suspect", suspects, "Suggest Suspect"));
      body.appendChild(buildSelect("suggest-weapon", weapons, "Suggest Weapon"));
      addActionButton(body, "Suggest", () => ({
        action: "suggest",
        suspect: document.getElementById("suggest-suspect").value,
        weapon: document.getElementById("suggest-weapon").value,
      }));
      actionControls.appendChild(card);
    }
    if (available.has("show_refute_card") || available.has("pass_refute")) {
      const { card, body } = createActionCard({
        eyebrow: "Private Response",
        title: "Resolve The Refute",
        detail: "Only the suggesting seat sees a shown card. Pass only if you cannot legally refute.",
        tone: "private",
      });
      if (available.has("show_refute_card")) {
        const cards = (legal.refute_cards || []).map((item) => ({ value: item, label: item }));
        body.appendChild(buildSelect("refute-card", cards, "Show Card"));
        addActionButton(body, "Show Refute Card", () => ({
          action: "show_refute_card",
          card: document.getElementById("refute-card").value,
        }));
      }
      if (available.has("pass_refute")) {
        addActionButton(body, "Pass Refute", () => ({ action: "pass_refute" }), "secondary-action");
      }
      actionControls.appendChild(card);
    }
    if (available.has("accuse")) {
      const suspects = snapshot.case_file_categories.suspect.map((item) => ({ value: item, label: item }));
      const weapons = snapshot.case_file_categories.weapon.map((item) => ({ value: item, label: item }));
      const rooms = snapshot.case_file_categories.room.map((item) => ({ value: item, label: item }));
      const { card, body } = createActionCard({
        eyebrow: "Final Call",
        title: "Make An Accusation",
        detail: "This can end your path to victory. Treat it as the highest-risk commit on the desk.",
        tone: "danger",
      });
      body.appendChild(buildSelect("accuse-suspect", suspects, "Accuse Suspect"));
      body.appendChild(buildSelect("accuse-weapon", weapons, "Accuse Weapon"));
      body.appendChild(buildSelect("accuse-room", rooms, "Accuse Room"));
      addActionButton(body, "Accuse", () => ({
        action: "accuse",
        suspect: document.getElementById("accuse-suspect").value,
        weapon: document.getElementById("accuse-weapon").value,
        room: document.getElementById("accuse-room").value,
      }), "danger-action");
      actionControls.appendChild(card);
    }
    if (available.has("end_turn")) {
      const { card, body } = createActionCard({
        eyebrow: "Wrap Up",
        title: "Close The Turn",
        detail: "Use this only after your planned movement, suggestion, and private review are complete.",
        tone: "secondary",
      });
      addActionButton(body, "End Turn", () => ({ action: "end_turn" }), "secondary-action");
      actionControls.appendChild(card);
    }
    if (!actionControls.children.length) {
      actionControls.innerHTML = '<p class="empty-state">No private actions are available from this seat right now.</p>';
    }
  }

  async function runSequencedRequest(work) {
    const sequence = ++requestSequence;
    const payload = await work();
    if (sequence < lastAppliedSequence) {
      return null;
    }
    lastAppliedSequence = sequence;
    return payload;
  }

  async function submitMutation(kind, path, payload) {
    if (pendingMutation) {
      return;
    }
    pendingMutation = kind;
    updateDraftControls();
    try {
      const snapshot = await runSequencedRequest(() => request(path, {
        method: "POST",
        body: JSON.stringify(payload),
      }));
      if (!snapshot) {
        return;
      }
      if (kind === "notebook") {
        notebookDirty = false;
      }
      if (kind === "chat") {
        chatInput.value = "";
        chatDirty = false;
        chatUnread = 0;
        forceChatScroll = true;
      }
      renderSummary(snapshot);
    } catch (error) {
      showError(error.message);
    } finally {
      pendingMutation = "";
      updateDraftControls();
      scheduleRefresh(220);
    }
  }

  async function refresh() {
    if (refreshing || pendingMutation) {
      scheduleRefresh(220);
      return;
    }
    refreshing = true;
    try {
      const path = eventCursor > 0
        ? `api/v1/games/current?since=${eventCursor}`
        : "api/v1/games/current";
      const snapshot = await runSequencedRequest(() => request(path));
      if (snapshot) {
        renderSummary(snapshot);
      }
    } catch (error) {
      showError(error.message);
    } finally {
      refreshing = false;
      scheduleRefresh(currentSnapshot);
    }
  }

  saveNotebook.addEventListener("click", async () => {
    await submitMutation("notebook", "api/v1/games/current/notebook", {
      notebook: { text: notebookText.value },
    });
  });

  sendChat.addEventListener("click", async () => {
    const text = chatInput.value.trim();
    if (!text) {
      return;
    }
    await submitMutation("chat", "api/v1/games/current/actions", {
      action: "send_chat",
      text,
    });
  });

  notebookText.addEventListener("input", () => {
    notebookDirty = true;
    updateDraftControls();
  });

  chatInput.addEventListener("input", () => {
    chatDirty = Boolean(chatInput.value.trim());
    updateDraftControls();
  });

  chatInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (!sendChat.disabled) {
        sendChat.click();
      }
    }
  });

  chatLog.addEventListener("scroll", () => {
    if (nearBottom(chatLog)) {
      chatUnread = 0;
      chatCount.textContent = String(eventsByChannel.chat.length);
      chatFeedCount.textContent = String(eventsByChannel.chat.length);
      chatCount.dataset.state = "calm";
      chatFeedCount.dataset.state = "calm";
    }
  });

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      refresh();
    }
  });

  updateDraftControls();
  refresh();
}
