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
  const turnBanner = document.getElementById("turn-banner");
  const phasePill = document.getElementById("phase-pill");
  const notebookText = document.getElementById("notebook-text");
  const chatInput = document.getElementById("chat-input");
  const saveNotebook = document.getElementById("save-notebook");
  const sendChat = document.getElementById("send-chat");
  const gameTitle = document.getElementById("game-title");
  const actionStatus = document.getElementById("action-status");
  const turnGuidance = document.getElementById("turn-guidance");
  const paceNote = document.getElementById("pace-note");
  const requestError = document.getElementById("request-error");
  const notebookStatus = document.getElementById("notebook-status");
  const narrativeCount = document.getElementById("narrative-count");
  const chatCount = document.getElementById("chat-count");
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
  const POLL_FAST_MS = 900;
  const POLL_NORMAL_MS = 2200;
  const POLL_IDLE_MS = 4200;

  let currentSnapshot = null;
  let notebookDirty = false;
  let chatDirty = false;
  let refreshTimer = null;
  let refreshing = false;
  const actionDrafts = new Map();

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

  function scheduleRefresh(snapshot = currentSnapshot) {
    if (refreshTimer) {
      window.clearTimeout(refreshTimer);
    }
    refreshTimer = window.setTimeout(() => {
      refresh();
    }, nextRefreshDelay(snapshot));
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
          <span class="card-kicker">Status</span>
          <strong>${snapshot.seat.can_win ? "Live case" : "Out of contention"}</strong>
        </article>
        <article class="summary-stat">
          <span class="card-kicker">Hand Size</span>
          <strong>${escapeHtml(snapshot.seat.hand_count)}</strong>
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

  function renderEventList(container, events, emptyMessage) {
    if (!events.length) {
      container.innerHTML = `<li class="empty-state">${escapeHtml(emptyMessage)}</li>`;
      return;
    }
    const orderedEvents = [...events].reverse();
    container.innerHTML = orderedEvents.map((event) => `
      <li class="log-item ${event.visibility === "public" ? "is-public" : "is-private"}">
        <div class="log-meta">
          <span class="log-badge">${escapeHtml(event.visibility === "public" ? "Public" : "Private")}</span>
          <span class="log-type">${escapeHtml(event.event_type.replaceAll("_", " "))}</span>
        </div>
        <p>${escapeHtml(event.message)}</p>
      </li>
    `).join("");
  }

  function isTraceEvent(event) {
    return String(event?.event_type || "").startsWith("trace_");
  }

  function publicNarrativeEvents(events) {
    return events.filter((event) => event.visibility === "public" && !isTraceEvent(event) && event.event_type !== "chat_posted");
  }

  function publicChatEvents(events) {
    return events.filter((event) => event.visibility === "public" && !isTraceEvent(event) && event.event_type === "chat_posted");
  }

  function renderGuidance(snapshot) {
    const available = new Set(snapshot.legal_actions?.available || []);
    const activeSeat = seatMap(snapshot).get(snapshot.active_seat_id);

    if (snapshot.status === "complete") {
      actionStatus.textContent = "Case Closed";
      turnGuidance.textContent = snapshot.winner_seat_id
        ? `${snapshot.winner_seat_id} won the game. You can keep reviewing the table record and private notes.`
        : "The case is closed.";
      return;
    }
    if (available.has("show_refute_card") || available.has("pass_refute")) {
      actionStatus.textContent = "Private Refute";
      turnGuidance.textContent = "You are being asked to refute a suggestion. Any shown card stays private to the suggesting seat.";
      return;
    }
    if (snapshot.active_seat_id === snapshot.seat.seat_id) {
      actionStatus.textContent = "Your Turn";
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
      turnGuidance.textContent = `Waiting on ${activeSeat.display_name}. Auto-refresh is running at a faster cadence until the autonomous turn settles.`;
      return;
    }
    actionStatus.textContent = "Waiting";
    turnGuidance.textContent = activeSeat
      ? `Waiting on ${activeSeat.display_name} to act.`
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
    notebookStatus.textContent = notebookDirty ? "Unsaved" : "Synced";
    saveNotebook.disabled = !notebookDirty;
    sendChat.disabled = !chatInput.value.trim();
  }

  function renderSummary(snapshot) {
    /*
      Re-render the full seat view from one authoritative snapshot while
      preserving any unsaved notebook/chat text and action draft selections.
    */
    const previousNotebook = notebookText.value;
    const previousChat = chatInput.value;
    currentSnapshot = snapshot;
    gameTitle.textContent = snapshot.title;
    turnBanner.textContent = snapshot.status === "complete"
      ? `Game complete. Winner: ${snapshot.winner_seat_id || "Unknown"}`
      : `Active seat: ${snapshot.active_seat_id}`;
    phasePill.textContent = snapshot.phase.replaceAll("_", " ");
    paceNote.textContent = waitingOnAutonomousSeat(snapshot)
      ? "Auto-refresh is in fast mode while an autonomous seat is acting."
      : "Auto-refresh is in steady mode for normal round-table play.";

    renderSeatSummary(snapshot);
    handList.innerHTML = snapshot.seat.hand.map((card) => `<li>${escapeHtml(card)}</li>`).join("");

    if (!notebookDirty) {
      notebookText.value = snapshot.notebook?.text || "";
    } else {
      notebookText.value = previousNotebook;
    }
    if (chatDirty) {
      chatInput.value = previousChat;
    }

    const narrativeEvents = publicNarrativeEvents(snapshot.events);
    const chatEvents = publicChatEvents(snapshot.events);
    const privateEvents = snapshot.events.filter((event) => event.visibility !== "public");

    narrativeCount.textContent = String(narrativeEvents.length);
    chatCount.textContent = String(chatEvents.length);
    privateCount.textContent = String(privateEvents.length);

    renderEventList(narrativeLog, narrativeEvents, "The public story of the game will appear here.");
    renderEventList(chatLog, chatEvents, "The table chat stream will appear here.");
    renderEventList(privateLog, privateEvents, "No private reveals or seat-only prompts yet.");
    renderSeatCards(snapshot);
    renderBoard(snapshot);
    renderPositionGrid(snapshot);
    renderActions(snapshot);
    renderGuidance(snapshot);
    renderSeatDebug(snapshot);
    renderAiExplainer(snapshot);
    updateDraftControls();
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

  function addActionButton(container, text, payloadBuilder, extraClass = "") {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = text;
    if (extraClass) {
      button.className = extraClass;
    }
    button.addEventListener("click", async () => {
      try {
        const payload = payloadBuilder();
        const snapshot = await request("api/v1/games/current/actions", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        chatDirty = false;
        renderSummary(snapshot);
      } catch (error) {
        showError(error.message);
      } finally {
        scheduleRefresh(currentSnapshot);
      }
    });
    container.appendChild(button);
  }

  function renderActions(snapshot) {
    // Action controls are rebuilt every refresh because legality changes by phase.
    const legal = snapshot.legal_actions || {};
    const available = new Set(legal.available || []);
    actionControls.innerHTML = "";

    if (available.has("roll")) {
      addActionButton(actionControls, "Roll", () => ({ action: "roll" }));
    }
    if (available.has("move") && (legal.move_targets || []).length) {
      const options = legal.move_targets.map((item) => ({ value: item.node_id, label: `${item.label} (${item.cost})` }));
      actionControls.appendChild(buildSelect("move-target", options, "Move To"));
      addActionButton(actionControls, "Move", () => ({
        action: "move",
        target_node: document.getElementById("move-target").value,
      }));
    }
    if (available.has("suggest")) {
      const suspects = snapshot.case_file_categories.suspect.map((item) => ({ value: item, label: item }));
      const weapons = snapshot.case_file_categories.weapon.map((item) => ({ value: item, label: item }));
      actionControls.appendChild(buildSelect("suggest-suspect", suspects, "Suggest Suspect"));
      actionControls.appendChild(buildSelect("suggest-weapon", weapons, "Suggest Weapon"));
      addActionButton(actionControls, "Suggest", () => ({
        action: "suggest",
        suspect: document.getElementById("suggest-suspect").value,
        weapon: document.getElementById("suggest-weapon").value,
      }));
    }
    if (available.has("show_refute_card")) {
      const cards = (legal.refute_cards || []).map((item) => ({ value: item, label: item }));
      actionControls.appendChild(buildSelect("refute-card", cards, "Show Card"));
      addActionButton(actionControls, "Show Refute Card", () => ({
        action: "show_refute_card",
        card: document.getElementById("refute-card").value,
      }));
    }
    if (available.has("pass_refute")) {
      addActionButton(actionControls, "Pass Refute", () => ({ action: "pass_refute" }), "secondary-action");
    }
    if (available.has("accuse")) {
      const suspects = snapshot.case_file_categories.suspect.map((item) => ({ value: item, label: item }));
      const weapons = snapshot.case_file_categories.weapon.map((item) => ({ value: item, label: item }));
      const rooms = snapshot.case_file_categories.room.map((item) => ({ value: item, label: item }));
      actionControls.appendChild(buildSelect("accuse-suspect", suspects, "Accuse Suspect"));
      actionControls.appendChild(buildSelect("accuse-weapon", weapons, "Accuse Weapon"));
      actionControls.appendChild(buildSelect("accuse-room", rooms, "Accuse Room"));
      addActionButton(actionControls, "Accuse", () => ({
        action: "accuse",
        suspect: document.getElementById("accuse-suspect").value,
        weapon: document.getElementById("accuse-weapon").value,
        room: document.getElementById("accuse-room").value,
      }), "danger-action");
    }
    if (available.has("end_turn")) {
      addActionButton(actionControls, "End Turn", () => ({ action: "end_turn" }), "secondary-action");
    }
    if (!actionControls.children.length) {
      actionControls.innerHTML = '<p class="empty-state">No private actions are available from this seat right now.</p>';
    }
  }

  async function refresh() {
    // Polling is the v1 synchronization mechanism for board state and event logs.
    if (refreshing) {
      return;
    }
    refreshing = true;
    try {
      const snapshot = await request("api/v1/games/current");
      renderSummary(snapshot);
    } catch (error) {
      showError(error.message);
    } finally {
      refreshing = false;
      scheduleRefresh(currentSnapshot);
    }
  }

  saveNotebook.addEventListener("click", async () => {
    try {
      const snapshot = await request("api/v1/games/current/notebook", {
        method: "POST",
        body: JSON.stringify({ notebook: { text: notebookText.value } }),
      });
      notebookDirty = false;
      renderSummary(snapshot);
    } catch (error) {
      showError(error.message);
    } finally {
      updateDraftControls();
      scheduleRefresh(currentSnapshot);
    }
  });

  sendChat.addEventListener("click", async () => {
    const text = chatInput.value.trim();
    if (!text) {
      return;
    }
    try {
      const snapshot = await request("api/v1/games/current/actions", {
        method: "POST",
        body: JSON.stringify({ action: "send_chat", text }),
      });
      chatInput.value = "";
      chatDirty = false;
      renderSummary(snapshot);
    } catch (error) {
      showError(error.message);
    } finally {
      updateDraftControls();
      scheduleRefresh(currentSnapshot);
    }
  });

  notebookText.addEventListener("input", () => {
    notebookDirty = true;
    updateDraftControls();
  });

  chatInput.addEventListener("input", () => {
    chatDirty = Boolean(chatInput.value.trim());
    updateDraftControls();
  });

  refresh();
}
