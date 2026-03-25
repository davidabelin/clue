const app = document.getElementById("game-app");

if (app) {
  const seatToken = app.dataset.seatToken;
  const eventLog = document.getElementById("event-log");
  const seatList = document.getElementById("seat-list");
  const handList = document.getElementById("hand-list");
  const seatSummary = document.getElementById("seat-summary");
  const board = document.getElementById("board");
  const moveHints = document.getElementById("move-hints");
  const actionControls = document.getElementById("action-controls");
  const turnBanner = document.getElementById("turn-banner");
  const phasePill = document.getElementById("phase-pill");
  const notebookText = document.getElementById("notebook-text");
  const chatInput = document.getElementById("chat-input");
  const saveNotebook = document.getElementById("save-notebook");
  const sendChat = document.getElementById("send-chat");
  const gameTitle = document.getElementById("game-title");
  let currentSnapshot = null;

  const COLORS = ["#d72638", "#e6af2e", "#f7f4ea", "#3a7d44", "#1d70a2", "#6a4c93"];

  async function request(path, options = {}) {
    const response = await fetch(path, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        "X-Clue-Seat-Token": seatToken,
        ...(options.headers || {}),
      },
    });
    return response.json();
  }

  function renderBoard(snapshot) {
    const highlights = new Set((snapshot.legal_actions.move_targets || []).map((item) => item.node_id));
    const seatPositions = {};
    snapshot.seats.forEach((seat) => {
      if (!seatPositions[seat.position]) {
        seatPositions[seat.position] = [];
      }
      seatPositions[seat.position].push(seat);
    });
    board.innerHTML = "";
    snapshot.board_nodes.forEach((node) => {
      const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
      g.setAttribute("class", `node ${node.kind}${highlights.has(node.id) ? " highlight" : ""}`);
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
        token.setAttribute("fill", COLORS[index % COLORS.length]);
        g.appendChild(token);
      });
      board.appendChild(g);
    });
    moveHints.innerHTML = (snapshot.legal_actions.move_targets || []).map((item) => `${item.label} (${item.cost})`).join(", ");
  }

  function renderSummary(snapshot) {
    currentSnapshot = snapshot;
    gameTitle.textContent = snapshot.title;
    turnBanner.textContent = snapshot.status === "complete"
      ? `Game complete. Winner: ${snapshot.winner_seat_id || "Unknown"}`
      : `Active seat: ${snapshot.active_seat_id}`;
    phasePill.textContent = snapshot.phase;
    seatSummary.innerHTML = `
      <p><strong>${snapshot.seat.display_name}</strong></p>
      <p>${snapshot.seat.character}</p>
      <p>Position: ${snapshot.seat.position}</p>
      <p>Can win: ${snapshot.seat.can_win ? "Yes" : "No"}</p>
    `;
    handList.innerHTML = snapshot.seat.hand.map((card) => `<li>${card}</li>`).join("");
    notebookText.value = snapshot.notebook?.text || "";
    seatList.innerHTML = snapshot.seats.map((seat) => `
      <li>
        <strong>${seat.display_name}</strong> (${seat.character})<br>
        ${seat.seat_kind} · ${seat.position} · ${seat.can_win ? "alive" : "eliminated"}
      </li>
    `).join("");
    eventLog.innerHTML = snapshot.events.map((event) => `<li>${event.message}</li>`).join("");
    renderBoard(snapshot);
    renderActions(snapshot);
  }

  function buildSelect(id, options, labelText, valueField = "value", textField = "label") {
    const wrapper = document.createElement("label");
    wrapper.className = "action-row";
    wrapper.innerHTML = `<span>${labelText}</span>`;
    const select = document.createElement("select");
    select.id = id;
    options.forEach((option) => {
      const item = document.createElement("option");
      item.value = option[valueField];
      item.textContent = option[textField];
      select.appendChild(item);
    });
    wrapper.appendChild(select);
    return wrapper;
  }

  function addActionButton(container, text, payloadBuilder) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = text;
    button.addEventListener("click", async () => {
      const payload = payloadBuilder();
      const snapshot = await request("/api/v1/games/current/actions", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      renderSummary(snapshot);
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
      const selectRow = buildSelect("move-target", options, "Move To");
      actionControls.appendChild(selectRow);
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
      addActionButton(actionControls, "Pass Refute", () => ({ action: "pass_refute" }));
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
      }));
    }
    if (available.has("end_turn")) {
      addActionButton(actionControls, "End Turn", () => ({ action: "end_turn" }));
    }
  }

  async function refresh() {
    const snapshot = await request("/api/v1/games/current");
    renderSummary(snapshot);
  }

  saveNotebook.addEventListener("click", async () => {
    const snapshot = await request("/api/v1/games/current/notebook", {
      method: "POST",
      body: JSON.stringify({ notebook: { text: notebookText.value } }),
    });
    renderSummary(snapshot);
  });

  sendChat.addEventListener("click", async () => {
    const text = chatInput.value.trim();
    if (!text) {
      return;
    }
    const snapshot = await request("/api/v1/games/current/actions", {
      method: "POST",
      body: JSON.stringify({ action: "send_chat", text }),
    });
    chatInput.value = "";
    renderSummary(snapshot);
  });

  refresh();
  window.setInterval(refresh, 2000);
}
