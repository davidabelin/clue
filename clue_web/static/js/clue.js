const app = document.getElementById("game-app");

if (app) {
  const seatToken = app.dataset.seatToken;
  const publicEventLog = document.getElementById("public-event-log");
  const privateLog = document.getElementById("private-log");
  const seatList = document.getElementById("seat-list");
  const handList = document.getElementById("hand-list");
  const seatSummary = document.getElementById("seat-summary");
  const board = document.getElementById("board");
  const moveGrid = document.getElementById("move-grid");
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
  const publicCount = document.getElementById("public-count");
  const privateCount = document.getElementById("private-count");

  const COLORS = ["#d43d51", "#d6a548", "#f3e1bc", "#4da163", "#5693d1", "#8f6ac7"];
  const POLL_FAST_MS = 900;
  const POLL_NORMAL_MS = 2200;
  const POLL_IDLE_MS = 4200;

  let currentSnapshot = null;
  let notebookDirty = false;
  let chatDirty = false;
  let refreshTimer = null;
  let refreshing = false;

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function seatMap(snapshot) {
    const map = new Map();
    snapshot.seats.forEach((seat, index) => {
      map.set(seat.seat_id, { ...seat, color: COLORS[index % COLORS.length] });
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
    const seatPositions = {};

    snapshot.seats.forEach((seat) => {
      if (!seatPositions[seat.position]) {
        seatPositions[seat.position] = [];
      }
      seatPositions[seat.position].push(seatsById.get(seat.seat_id));
    });

    board.innerHTML = "";
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
      board.appendChild(g);
    });
  }

  function renderMoveGrid(snapshot) {
    const targets = snapshot.legal_actions.move_targets || [];
    if (!targets.length) {
      moveGrid.innerHTML = '<p class="empty-state">No movement options are open right now.</p>';
      return;
    }
    moveGrid.innerHTML = targets.map((item) => `
      <article class="move-card move-mode-${escapeHtml(item.mode || "walk")}">
        <p class="card-kicker">${escapeHtml(item.mode === "passage" ? "Secret Passage" : "Movement")}</p>
        <h4>${escapeHtml(item.label)}</h4>
        <p>${item.mode === "passage" ? "Instant room transfer." : `Cost: ${escapeHtml(item.cost)}`}</p>
      </article>
    `).join("");
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
          <p class="card-kicker">${escapeHtml(seat.seat_kind)}</p>
          <h4>${escapeHtml(seat.display_name)}</h4>
          <p>${escapeHtml(seat.character)}</p>
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
              <p>${escapeHtml(seat.character)}</p>
            </div>
          </div>
          <p>${escapeHtml(labels.get(seat.position) || seat.position)}</p>
          <p>${escapeHtml(seat.seat_kind)} · ${seat.can_win ? "alive" : "eliminated"}</p>
        </article>
      `;
    }).join("");
  }

  function renderEventList(container, events, emptyMessage) {
    if (!events.length) {
      container.innerHTML = `<li class="empty-state">${escapeHtml(emptyMessage)}</li>`;
      return;
    }
    container.innerHTML = events.map((event) => `
      <li class="log-item ${event.visibility === "public" ? "is-public" : "is-private"}">
        <div class="log-meta">
          <span class="log-badge">${escapeHtml(event.visibility === "public" ? "Public" : "Private")}</span>
          <span class="log-type">${escapeHtml(event.event_type.replaceAll("_", " "))}</span>
        </div>
        <p>${escapeHtml(event.message)}</p>
      </li>
    `).join("");
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
        turnGuidance.textContent = "Choose a legal destination from the move grid or the action controls.";
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

  function updateDraftControls() {
    notebookStatus.textContent = notebookDirty ? "Unsaved" : "Synced";
    saveNotebook.disabled = !notebookDirty;
    sendChat.disabled = !chatInput.value.trim();
  }

  function renderSummary(snapshot) {
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

    const publicEvents = snapshot.events.filter((event) => event.visibility === "public");
    const privateEvents = snapshot.events.filter((event) => event.visibility !== "public");

    publicCount.textContent = String(publicEvents.length);
    privateCount.textContent = String(privateEvents.length);

    renderEventList(publicEventLog, publicEvents, "The public table record will appear here.");
    renderEventList(privateLog, privateEvents, "No private reveals or seat-only prompts yet.");
    renderSeatCards(snapshot);
    renderBoard(snapshot);
    renderMoveGrid(snapshot);
    renderPositionGrid(snapshot);
    renderActions(snapshot);
    renderGuidance(snapshot);
    updateDraftControls();
  }

  function buildSelect(id, options, labelText, valueField = "value", textField = "label") {
    const previous = document.getElementById(id);
    const previousValue = previous ? previous.value : "";
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
        const snapshot = await request("/api/v1/games/current/actions", {
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
    if (refreshing) {
      return;
    }
    refreshing = true;
    try {
      const snapshot = await request("/api/v1/games/current");
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
      const snapshot = await request("/api/v1/games/current/notebook", {
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
      const snapshot = await request("/api/v1/games/current/actions", {
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
