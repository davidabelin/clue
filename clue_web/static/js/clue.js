
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
    study: "#e0d6f2", hall: "#efe1c5", lounge: "#f3cfc2", library: "#d6e6cf",
    billiard: "#cfe1d7", dining: "#f0dbc0", conservatory: "#d7ecd7", ballroom: "#e7d8ef", kitchen: "#f5e0bd",
  };

  const POLL_FAST_MS = 900;
  const POLL_NORMAL_MS = 2200;
  const POLL_IDLE_MS = 4200;
  const SSE_RETRY_BASE_MS = 1800;
  const SSE_RETRY_MAX_MS = 14000;

  const state = {
    snapshot: null,
    snapshotKey: "",
    eventCursor: 0,
    notebookDirty: false,
    chatDirty: false,
    actionDrafts: new Map(),
    legalFingerprint: "",
    mutationInFlight: 0,
    pendingMutation: "",
    refreshing: false,
    pollEnabled: false,
    refreshTimer: null,
    readRequestId: 0,
    lastReadAppliedId: 0,
    writeRequestId: 0,
    lastWriteAppliedId: 0,
    sse: null,
    sseRetryTimer: null,
    sseFailureCount: 0,
    syncMode: "boot",
    seenEventIndices: new Set(),
    eventsByChannel: { narrative: [], chat: [], private: [] },
    chatUnread: 0,
  };

  function escapeHtml(value) {
    return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
  }

  function seatKindLabel(seatKind) {
    const v = String(seatKind ?? "").trim().toLowerCase();
    if (v === "llm" || v === "heuristic") return "LLM";
    if (v === "human") return "Human";
    if (v === "np") return "NP";
    return v || "Unknown";
  }

  function seatMap(snapshot) {
    const map = new Map();
    snapshot.seats.forEach((seat) => map.set(seat.seat_id, { ...seat, color: CHARACTER_COLORS[seat.character] || "#8e2331" }));
    return map;
  }

  function boardLabelById(snapshot) {
    const map = new Map();
    snapshot.board_nodes.forEach((node) => map.set(node.id, node.label));
    return map;
  }

  function boardNodeById(snapshot) {
    const map = new Map();
    snapshot.board_nodes.forEach((node) => map.set(node.id, node));
    return map;
  }

  function cursorFromSnapshot(snapshot) {
    const cursor = Number(snapshot?.event_cursor || 0);
    const maxEvent = Math.max(0, ...(snapshot?.events || []).map((event) => Number(event.event_index || 0)));
    return Math.max(cursor, maxEvent);
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
  function setMoveDraft(nodeId) {
    const select = document.getElementById("move-target");
    if (select) {
      select.value = nodeId;
    }
    state.actionDrafts.set("move-target", nodeId);
  }

  function waitingOnAutonomousSeat(snapshot) {
    if (!snapshot || snapshot.status !== "active") return false;
    if (snapshot.active_seat_id === snapshot.seat.seat_id) return false;
    const available = new Set(snapshot.legal_actions?.available || []);
    if (available.has("show_refute_card") || available.has("pass_refute")) return false;
    const activeSeat = seatMap(snapshot).get(snapshot.active_seat_id);
    return Boolean(activeSeat && activeSeat.seat_kind !== "human");
  }

  function nextPollDelay(snapshot = state.snapshot) {
    if (document.hidden) return POLL_IDLE_MS;
    if (!snapshot) return POLL_NORMAL_MS;
    if (snapshot.status !== "active") return POLL_IDLE_MS;
    return waitingOnAutonomousSeat(snapshot) ? POLL_FAST_MS : POLL_NORMAL_MS;
  }

  function renderBoard(snapshot) {
    const highlights = new Set((snapshot.legal_actions.move_targets || []).map((item) => item.node_id));
    const seatsById = seatMap(snapshot);
    const nodesById = boardNodeById(snapshot);
    const seatPositions = {};
    const surface = document.createElementNS("http://www.w3.org/2000/svg", "g");
    surface.setAttribute("transform", "translate(58 42) scale(0.78)");

    snapshot.seats.forEach((seat) => {
      if (!seatPositions[seat.position]) seatPositions[seat.position] = [];
      seatPositions[seat.position].push(seatsById.get(seat.seat_id));
    });

    board.innerHTML = "";
    (snapshot.board_edges || []).forEach((edge) => {
      const from = nodesById.get(edge.from);
      const to = nodesById.get(edge.to);
      if (!from || !to) return;
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
      const classes = ["node", node.kind];
      if (highlights.has(node.id)) classes.push("highlight");
      if (snapshot.seat.position === node.id) classes.push("current-seat-node");
      g.setAttribute("class", classes.join(" "));

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
        g.addEventListener("click", () => setMoveDraft(node.id));
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
      if (seat.seat_id === snapshot.active_seat_id) classes.push("is-active");
      if (seat.seat_id === snapshot.seat.seat_id) classes.push("is-you");
      if (!seat.can_win) classes.push("is-out");
      return `
        <article class="${classes.join(" ")}">
          <p class="card-kicker">${escapeHtml(seatKindLabel(seat.seat_kind))}</p>
          <h4>${escapeHtml(seat.display_name)}</h4>
          <p>${escapeHtml(labels.get(seat.position) || seat.position)}</p>
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
        <article class="summary-stat"><span class="card-kicker">Character</span><strong>${escapeHtml(snapshot.seat.character)}</strong></article>
        <article class="summary-stat"><span class="card-kicker">Marker</span><strong>${escapeHtml(labels.get(snapshot.seat.position) || snapshot.seat.position)}</strong></article>
        <article class="summary-stat"><span class="card-kicker">Status</span><strong>${snapshot.seat.can_win ? "Live case" : "Out of contention"}</strong></article>
        <article class="summary-stat"><span class="card-kicker">Hand Size</span><strong>${escapeHtml(snapshot.seat.hand_count)}</strong></article>
      </div>
    `;
  }
  function renderSeatCards(snapshot) {
    const labels = boardLabelById(snapshot);
    const seatsById = seatMap(snapshot);
    seatList.innerHTML = snapshot.seats.map((seat) => {
      const decorated = seatsById.get(seat.seat_id);
      const classes = ["seat-card"];
      if (seat.seat_id === snapshot.active_seat_id) classes.push("is-active");
      if (seat.seat_id === snapshot.seat.seat_id) classes.push("is-you");
      if (!seat.can_win) classes.push("is-out");
      return `
        <article class="${classes.join(" ")}">
          <div class="seat-card-head">
            <span class="seat-swatch" style="--seat-color: ${escapeHtml(decorated.color)}"></span>
            <div>
              <h3>${escapeHtml(seat.display_name)}</h3>
              <p>${escapeHtml(seatKindLabel(seat.seat_kind))}</p>
            </div>
          </div>
          <p>${escapeHtml(labels.get(seat.position) || seat.position)}</p>
          <p>${seat.can_win ? "alive" : "eliminated"}</p>
        </article>
      `;
    }).join("");
  }

  function renderGuidance(snapshot) {
    const available = new Set(snapshot.legal_actions?.available || []);
    const activeSeat = seatMap(snapshot).get(snapshot.active_seat_id);

    if (snapshot.status === "complete") {
      actionStatus.textContent = "Case Closed";
      turnGuidance.textContent = snapshot.winner_seat_id
        ? `${snapshot.winner_seat_id} won the game. Review the record and private notes.`
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
        turnGuidance.textContent = "Open the turn with a roll, or use a secret passage if available.";
      } else if (available.has("move")) {
        turnGuidance.textContent = "Choose a legal destination and confirm Move.";
      } else if (available.has("suggest")) {
        turnGuidance.textContent = "You can suggest from your current room.";
      } else if (available.has("accuse")) {
        turnGuidance.textContent = "Accusations end the question immediately.";
      } else {
        turnGuidance.textContent = "Review state and finish your turn when ready.";
      }
      return;
    }
    if (activeSeat && activeSeat.seat_kind !== "human") {
      actionStatus.textContent = "AI Seat Acting";
      turnGuidance.textContent = `Waiting on ${activeSeat.display_name}.`;
      return;
    }
    actionStatus.textContent = "Waiting";
    turnGuidance.textContent = activeSeat ? `Waiting on ${activeSeat.display_name} to act.` : "Waiting on the next seat.";
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
      seatDebug.innerHTML = "<p class=\"empty-state\">No private agent-debug payload has been recorded for this seat yet.</p>";
      return;
    }

    debugStatus.textContent = metric?.fallback_used ? "Fallback" : "Live";
    seatDebug.innerHTML = `
      <div class="debug-grid">
        <article class="summary-stat"><span class="card-kicker">Joint Entropy</span><strong>${escapeHtml(toolSnapshot.belief_summary?.joint_case_entropy_bits ?? "--")}</strong></article>
        <article class="summary-stat"><span class="card-kicker">Accuse Confidence</span><strong>${escapeHtml(accusation.confidence ?? "--")}</strong></article>
        <article class="summary-stat"><span class="card-kicker">Last Action</span><strong>${escapeHtml(debug.decision?.action || metric?.action || "--")}</strong></article>
        <article class="summary-stat"><span class="card-kicker">Latency</span><strong>${escapeHtml(metric?.latency_ms ?? "--")} ms</strong></article>
      </div>
      <div class="debug-block"><p class="card-kicker">Top Hypotheses</p><ul class="debug-list">${topHypotheses.length ? topHypotheses.map((item) => `<li>${escapeHtml(`${item.suspect} / ${item.weapon} / ${item.room} (${item.p})`)}</li>`).join("") : "<li>No hypothesis sample yet.</li>"}</ul></div>
      <div class="debug-block"><p class="card-kicker">Top Suggestion</p><p>${escapeHtml(topSuggestions[0]?.why || debug.decision_debug?.model_rationale || "No suggestion ranking yet.")}</p></div>
    `;
  }

  function renderAiExplainer(snapshot) {
    const metrics = snapshot.analysis?.game_metrics || {};
    const targets = snapshot.analysis?.latency_targets_ms || {};
    aiExplainer.innerHTML = `
      <p class="field-note">LLM seats use filtered seat-local context and fallback to deterministic heuristics when needed.</p>
      <div class="debug-grid">
        <article class="summary-stat"><span class="card-kicker">Fallback Rate</span><strong>${escapeHtml(metrics.fallback_rate ?? 0)}</strong></article>
        <article class="summary-stat"><span class="card-kicker">Guardrail Blocks</span><strong>${escapeHtml(metrics.guardrail_blocks ?? 0)}</strong></article>
        <article class="summary-stat"><span class="card-kicker">Tool Budget</span><strong>${escapeHtml(targets.tool_snapshot_ms ?? "--")} ms</strong></article>
        <article class="summary-stat"><span class="card-kicker">LLM Budget</span><strong>${escapeHtml(targets.llm_turn_ms ?? "--")} ms</strong></article>
      </div>
    `;
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

  function renderEventItem(event) {
    const li = document.createElement("li");
    li.className = `log-item ${event.visibility === "public" ? "is-public" : "is-private"}`;
    li.dataset.eventIndex = String(event.event_index || "");
    li.innerHTML = `
      <div class="log-meta">
        <span class="log-badge">${escapeHtml(event.visibility === "public" ? "Public" : "Private")}</span>
        <span class="log-type">${escapeHtml(String(event.event_type || "").replaceAll("_", " "))}</span>
      </div>
      <p>${escapeHtml(event.message)}</p>
    `;
    return li;
  }

  function nearBottom(container, threshold = 40) {
    return (container.scrollHeight - (container.scrollTop + container.clientHeight)) <= threshold;
  }
  function appendEvents(container, events, { chatChannel = false } = {}) {
    if (!events.length) return;
    const stick = nearBottom(container);
    const empty = container.querySelector(".empty-state");
    if (empty) empty.remove();
    const fragment = document.createDocumentFragment();
    events.forEach((event) => fragment.appendChild(renderEventItem(event)));
    container.appendChild(fragment);
    if (chatChannel && !stick) state.chatUnread += events.length;
    if (stick) {
      container.scrollTop = container.scrollHeight;
      if (chatChannel) state.chatUnread = 0;
    }
  }

  function ensureEmpty(container, items, message) {
    if (items.length > 0) return;
    container.innerHTML = `<li class="empty-state">${escapeHtml(message)}</li>`;
  }

  function ingestEvents(events) {
    const appended = { narrative: [], chat: [], private: [] };
    [...events].sort((a, b) => Number(a.event_index || 0) - Number(b.event_index || 0)).forEach((event) => {
      const index = Number(event.event_index || 0);
      if (!Number.isFinite(index) || index <= 0 || state.seenEventIndices.has(index)) return;
      const channel = eventChannel(event);
      state.seenEventIndices.add(index);
      if (channel === "ignore") return;
      state.eventsByChannel[channel].push(event);
      appended[channel].push(event);
    });
    return appended;
  }

  function renderEventPanels(appended) {
    appendEvents(narrativeLog, appended.narrative);
    appendEvents(chatLog, appended.chat, { chatChannel: true });
    appendEvents(privateLog, appended.private);

    ensureEmpty(narrativeLog, state.eventsByChannel.narrative, "The public story of the game will appear here.");
    ensureEmpty(chatLog, state.eventsByChannel.chat, "The table chat stream will appear here.");
    ensureEmpty(privateLog, state.eventsByChannel.private, "No private reveals or seat-only prompts yet.");

    narrativeCount.textContent = String(state.eventsByChannel.narrative.length);
    const totalChat = state.eventsByChannel.chat.length;
    chatCount.textContent = state.chatUnread > 0 ? `${totalChat} (+${state.chatUnread})` : String(totalChat);
    privateCount.textContent = String(state.eventsByChannel.private.length);
  }

  function buildSelect(id, options, labelText, valueField = "value", textField = "label") {
    const previousValue = state.actionDrafts.get(id) || "";
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
    state.actionDrafts.set(id, select.value);
    select.addEventListener("change", () => {
      state.actionDrafts.set(id, select.value);
    });
    wrapper.appendChild(select);
    return wrapper;
  }

  function addActionButton(container, text, payloadBuilder, extraClass = "") {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.actionButton = "1";
    button.textContent = text;
    if (extraClass) button.className = extraClass;
    button.addEventListener("click", async () => {
      await submitMutation("action", "api/v1/games/current/actions", payloadBuilder());
    });
    container.appendChild(button);
  }

  function renderActions(snapshot) {
    const legal = snapshot.legal_actions || {};
    const available = new Set(legal.available || []);
    actionControls.innerHTML = "";

    if (available.has("roll")) addActionButton(actionControls, "Roll", () => ({ action: "roll" }));
    if (available.has("move") && (legal.move_targets || []).length) {
      const options = legal.move_targets.map((item) => ({ value: item.node_id, label: `${item.label} (${item.cost})` }));
      actionControls.appendChild(buildSelect("move-target", options, "Move To"));
      addActionButton(actionControls, "Move", () => ({ action: "move", target_node: document.getElementById("move-target").value }));
    }
    if (available.has("suggest")) {
      const suspects = snapshot.case_file_categories.suspect.map((item) => ({ value: item, label: item }));
      const weapons = snapshot.case_file_categories.weapon.map((item) => ({ value: item, label: item }));
      actionControls.appendChild(buildSelect("suggest-suspect", suspects, "Suggest Suspect"));
      actionControls.appendChild(buildSelect("suggest-weapon", weapons, "Suggest Weapon"));
      addActionButton(actionControls, "Suggest", () => ({ action: "suggest", suspect: document.getElementById("suggest-suspect").value, weapon: document.getElementById("suggest-weapon").value }));
    }
    if (available.has("show_refute_card")) {
      const cards = (legal.refute_cards || []).map((item) => ({ value: item, label: item }));
      actionControls.appendChild(buildSelect("refute-card", cards, "Show Card"));
      addActionButton(actionControls, "Show Refute Card", () => ({ action: "show_refute_card", card: document.getElementById("refute-card").value }));
    }
    if (available.has("pass_refute")) addActionButton(actionControls, "Pass Refute", () => ({ action: "pass_refute" }), "secondary-action");
    if (available.has("accuse")) {
      const suspects = snapshot.case_file_categories.suspect.map((item) => ({ value: item, label: item }));
      const weapons = snapshot.case_file_categories.weapon.map((item) => ({ value: item, label: item }));
      const rooms = snapshot.case_file_categories.room.map((item) => ({ value: item, label: item }));
      actionControls.appendChild(buildSelect("accuse-suspect", suspects, "Accuse Suspect"));
      actionControls.appendChild(buildSelect("accuse-weapon", weapons, "Accuse Weapon"));
      actionControls.appendChild(buildSelect("accuse-room", rooms, "Accuse Room"));
      addActionButton(actionControls, "Accuse", () => ({ action: "accuse", suspect: document.getElementById("accuse-suspect").value, weapon: document.getElementById("accuse-weapon").value, room: document.getElementById("accuse-room").value }), "danger-action");
    }
    if (available.has("end_turn")) addActionButton(actionControls, "End Turn", () => ({ action: "end_turn" }), "secondary-action");
    if (!actionControls.children.length) actionControls.innerHTML = '<p class="empty-state">No private actions are available from this seat right now.</p>';
  }

  function updatePaceNote(snapshot) {
    const mode = state.syncMode === "sse"
      ? "Live stream connected."
      : state.syncMode.startsWith("poll")
        ? "Live stream reconnecting; cursor polling active."
        : "Synchronizing table state.";
    const pace = waitingOnAutonomousSeat(snapshot)
      ? " Fast cadence while an autonomous seat is acting."
      : " Standard cadence for round-table play.";
    paceNote.textContent = `${mode}${pace}`;
  }

  function updateDraftControls() {
    notebookStatus.textContent = state.notebookDirty ? "Unsaved" : "Synced";
    saveNotebook.disabled = !state.notebookDirty || state.mutationInFlight > 0;
    sendChat.disabled = !chatInput.value.trim() || state.mutationInFlight > 0;
    saveNotebook.textContent = state.pendingMutation === "notebook" ? "Saving..." : "Save Notebook";
    sendChat.textContent = state.pendingMutation === "chat" ? "Sending..." : "Send Chat";
    actionControls.querySelectorAll("button[data-action-button='1']").forEach((button) => {
      button.disabled = state.mutationInFlight > 0;
    });
  }

  function applySnapshot(snapshot, source = "read") {
    const cursor = cursorFromSnapshot(snapshot);
    if (cursor < state.eventCursor) return;

    const key = `${cursor}:${snapshot.turn_index}:${snapshot.phase}:${snapshot.active_seat_id}`;
    if (source !== "write" && cursor === state.eventCursor && key === state.snapshotKey) return;

    state.snapshot = snapshot;
    state.snapshotKey = key;
    state.eventCursor = cursor;

    gameTitle.textContent = snapshot.title;
    turnBanner.textContent = snapshot.status === "complete"
      ? `Game complete. Winner: ${snapshot.winner_seat_id || "Unknown"}`
      : `Active seat: ${snapshot.active_seat_id}`;
    phasePill.textContent = String(snapshot.phase || "").replaceAll("_", " ");

    renderSeatSummary(snapshot);
    handList.innerHTML = snapshot.seat.hand.map((card) => `<li>${escapeHtml(card)}</li>`).join("");
    if (!state.notebookDirty) notebookText.value = snapshot.notebook?.text || "";

    const appended = ingestEvents(snapshot.events || []);
    renderEventPanels(appended);

    renderSeatCards(snapshot);
    renderBoard(snapshot);
    renderPositionGrid(snapshot);
    const legalFingerprint = JSON.stringify(snapshot.legal_actions || {});
    if (legalFingerprint !== state.legalFingerprint) {
      state.legalFingerprint = legalFingerprint;
      renderActions(snapshot);
    }
    renderGuidance(snapshot);
    renderSeatDebug(snapshot);
    renderAiExplainer(snapshot);
    updatePaceNote(snapshot);
    updateDraftControls();
  }
  async function submitMutation(kind, path, payload, onSuccess = () => {}) {
    const writeId = ++state.writeRequestId;
    state.mutationInFlight += 1;
    state.pendingMutation = kind;
    updateDraftControls();

    try {
      const snapshot = await request(path, { method: "POST", body: JSON.stringify(payload) });
      if (writeId >= state.lastWriteAppliedId) {
        state.lastWriteAppliedId = writeId;
        onSuccess();
        applySnapshot(snapshot, "write");
      }
    } catch (error) {
      showError(error.message);
    } finally {
      state.mutationInFlight = Math.max(0, state.mutationInFlight - 1);
      if (state.mutationInFlight === 0) state.pendingMutation = "";
      updateDraftControls();
      if (!state.sse) schedulePolling(280);
    }
  }

  function schedulePolling(delayMs = nextPollDelay()) {
    if (!state.pollEnabled) return;
    if (state.refreshTimer) window.clearTimeout(state.refreshTimer);
    state.refreshTimer = window.setTimeout(() => refreshFromPolling(), delayMs);
  }

  function stopPolling() {
    state.pollEnabled = false;
    if (state.refreshTimer) {
      window.clearTimeout(state.refreshTimer);
      state.refreshTimer = null;
    }
  }

  function startPolling(reason = "fallback") {
    state.syncMode = `poll:${reason}`;
    state.pollEnabled = true;
    schedulePolling(200);
    if (state.snapshot) updatePaceNote(state.snapshot);
  }

  async function refreshFromPolling() {
    if (!state.pollEnabled || state.refreshing || state.sse) return;
    if (state.mutationInFlight > 0) {
      schedulePolling(220);
      return;
    }

    state.refreshing = true;
    const requestId = ++state.readRequestId;
    try {
      const snapshot = await request(`api/v1/games/current?since=${state.eventCursor}`);
      if (requestId >= state.lastReadAppliedId) {
        state.lastReadAppliedId = requestId;
        applySnapshot(snapshot, "poll");
      }
    } catch (error) {
      showError(error.message);
    } finally {
      state.refreshing = false;
      schedulePolling();
    }
  }

  function stopSse() {
    if (state.sse) {
      state.sse.close();
      state.sse = null;
    }
    if (state.sseRetryTimer) {
      window.clearTimeout(state.sseRetryTimer);
      state.sseRetryTimer = null;
    }
  }

  function scheduleSseRetry() {
    if (state.sseRetryTimer) window.clearTimeout(state.sseRetryTimer);
    const delay = Math.min(SSE_RETRY_BASE_MS * (2 ** Math.max(state.sseFailureCount - 1, 0)), SSE_RETRY_MAX_MS);
    state.sseRetryTimer = window.setTimeout(() => startSse(), delay);
  }

  function startSse() {
    if (!("EventSource" in window)) {
      startPolling("no-eventsource");
      return;
    }

    stopSse();
    const source = new EventSource(`api/v1/games/current/stream?token=${encodeURIComponent(seatToken)}&since=${state.eventCursor}`);
    state.sse = source;

    source.onopen = () => {
      state.syncMode = "sse";
      state.sseFailureCount = 0;
      stopPolling();
      if (state.snapshot) updatePaceNote(state.snapshot);
    };

    const handleSnapshot = (event) => {
      try {
        applySnapshot(JSON.parse(event.data || "{}"), "sse");
      } catch {
        showError("Received an invalid realtime update payload.");
      }
    };

    source.addEventListener("snapshot", handleSnapshot);
    source.onmessage = handleSnapshot;

    source.onerror = () => {
      if (state.sse !== source) return;
      source.close();
      state.sse = null;
      state.sseFailureCount += 1;
      startPolling("sse-disconnected");
      scheduleSseRetry();
    };
  }

  function bindInputs() {
    notebookText.addEventListener("input", () => {
      state.notebookDirty = true;
      updateDraftControls();
    });

    chatInput.addEventListener("input", () => {
      state.chatDirty = Boolean(chatInput.value.trim());
      updateDraftControls();
    });

    chatInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        if (!sendChat.disabled) sendChat.click();
      }
    });

    chatLog.addEventListener("scroll", () => {
      if (nearBottom(chatLog)) {
        state.chatUnread = 0;
        chatCount.textContent = String(state.eventsByChannel.chat.length);
      }
    });

    saveNotebook.addEventListener("click", async () => {
      await submitMutation("notebook", "api/v1/games/current/notebook", { notebook: { text: notebookText.value } }, () => {
        state.notebookDirty = false;
      });
    });

    sendChat.addEventListener("click", async () => {
      const text = chatInput.value.trim();
      if (!text) return;
      await submitMutation("chat", "api/v1/games/current/actions", { action: "send_chat", text }, () => {
        chatInput.value = "";
        state.chatDirty = false;
        state.chatUnread = 0;
      });
    });

    document.addEventListener("visibilitychange", () => {
      if (!document.hidden && !state.sse) {
        request(`api/v1/games/current?since=${state.eventCursor}`).then((snapshot) => {
          applySnapshot(snapshot, "poll");
        }).catch((error) => {
          showError(error.message);
        });
      }
    });
  }

  async function bootstrap() {
    bindInputs();
    updateDraftControls();

    try {
      const snapshot = await request("api/v1/games/current");
      applySnapshot(snapshot, "boot");
      startSse();
      if (!state.sse) startPolling("bootstrap");
    } catch (error) {
      showError(error.message);
      startPolling("bootstrap-error");
    }
  }

  bootstrap();
}
