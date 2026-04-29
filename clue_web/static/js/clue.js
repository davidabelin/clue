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
  const turnRail = document.getElementById("turn-rail");
  const actionPriorityNote = document.getElementById("action-priority-note");
  const paceNote = document.getElementById("pace-note");
  const requestError = document.getElementById("request-error");
  const notebookStatus = document.getElementById("notebook-status");
  const notebookDraftState = document.getElementById("notebook-draft-state");
  const seatStatePill = document.getElementById("seat-state-pill");
  const narrativeCount = document.getElementById("narrative-count");
  const chatCount = document.getElementById("chat-count");
  const chatDraftState = document.getElementById("chat-draft-state");
  const chatFeedCount = document.getElementById("chat-feed-count");
  const privateCount = document.getElementById("private-count");
  const seatDebug = document.getElementById("seat-debug");
  const debugStatus = document.getElementById("debug-status");
  const aiExplainer = document.getElementById("ai-explainer");
  const privateIntelDrawer = document.querySelector("details[data-collapse-key='private-intel']");

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
  const COLLAPSE_STORAGE_PREFIX = "clue.beginner.collapse.";
  const PLAYER_NARRATIVE_TYPES = new Set([
    "suggestion_made",
    "suggestion_refuted",
    "suggestion_unanswered",
    "accusation_made",
    "accusation_wrong",
    "accusation_correct",
  ]);

  let currentSnapshot = null;
  let currentUiMode = "beginner";
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
  let renderFingerprints = new Map();
  const clientTelemetry = {
    lastFetchMs: 0,
    lastRenderMs: 0,
    renderCounts: {},
  };
  // Draft state is kept outside the latest snapshot so polling can redraw from
  // server-authoritative data without clobbering in-progress human input.
  const actionDrafts = new Map();
  const seenEventIndices = new Set();
  const eventsByChannel = {
    narrative: [],
    chat: [],
    private: [],
  };

  function readStoredBoolean(key) {
    try {
      const raw = window.localStorage.getItem(key);
      if (raw === null) {
        return null;
      }
      return raw === "1";
    } catch {
      return null;
    }
  }

  function writeStoredBoolean(key, value) {
    try {
      window.localStorage.setItem(key, value ? "1" : "0");
    } catch {
      // Ignore storage failures so private browsing or locked-down browsers do
      // not break the table UI.
    }
  }

  function collapseStorageKey(collapseKey) {
    return `${COLLAPSE_STORAGE_PREFIX}${collapseKey}`;
  }

  function collapsePreference(collapseKey) {
    return readStoredBoolean(collapseStorageKey(collapseKey));
  }

  function rememberCollapsible(node) {
    if (!node || node.dataset.collapseBound === "1") {
      return;
    }
    node.addEventListener("toggle", () => {
      if (node.dataset.suspendCollapseSave === "1") {
        return;
      }
      writeStoredBoolean(collapseStorageKey(node.dataset.collapseKey), node.open);
    });
    node.dataset.collapseBound = "1";
  }

  function applyCollapsibleState(node, fallbackOpen) {
    if (!node) {
      return;
    }
    rememberCollapsible(node);
    const stored = collapsePreference(node.dataset.collapseKey);
    const desired = stored ?? Boolean(fallbackOpen);
    if (node.open !== desired) {
      node.dataset.suspendCollapseSave = "1";
      node.open = desired;
      window.setTimeout(() => {
        delete node.dataset.suspendCollapseSave;
      }, 0);
    }
  }

  function initCollapsibleSections() {
    document.querySelectorAll("details[data-collapse-key]").forEach((node) => {
      const fallbackOpen = node.dataset.defaultOpen === "1";
      if (node.dataset.openWhenPopulated === "1") {
        rememberCollapsible(node);
        return;
      }
      applyCollapsibleState(node, fallbackOpen);
    });
  }

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

  function normalizeUiMode(value) {
    return String(value || "").trim().toLowerCase() === "player" ? "player" : "beginner";
  }

  function isPlayerMode() {
    return currentUiMode === "player";
  }

  function applyUiMode(snapshot) {
    const nextUiMode = normalizeUiMode(snapshot?.ui_mode);
    if (currentUiMode !== nextUiMode) {
      renderFingerprints = new Map();
    }
    currentUiMode = nextUiMode;
    app.dataset.uiMode = currentUiMode;
    app.classList.toggle("game-app--player", isPlayerMode());
    app.classList.toggle("game-app--beginner", !isPlayerMode());
  }

  function titleize(value) {
    return String(value ?? "")
      .replaceAll("_", " ")
      .replace(/\b\w/g, (match) => match.toUpperCase());
  }

  function nowMs() {
    return typeof performance !== "undefined" && typeof performance.now === "function"
      ? performance.now()
      : Date.now();
  }

  function roundedMs(value) {
    return Math.max(0, Math.round(Number(value || 0)));
  }

  function stableFingerprint(value) {
    try {
      return JSON.stringify(value);
    } catch {
      return String(value ?? "");
    }
  }

  function renderIfChanged(key, fingerprint, renderFn) {
    const next = stableFingerprint(fingerprint);
    if (renderFingerprints.get(key) === next) {
      return false;
    }
    const started = nowMs();
    renderFn();
    renderFingerprints.set(key, next);
    clientTelemetry.renderCounts[key] = (clientTelemetry.renderCounts[key] || 0) + 1;
    const elapsed = roundedMs(nowMs() - started);
    if (elapsed > 80 && typeof console !== "undefined" && typeof console.debug === "function") {
      console.debug(`[clue] slow ${key} render`, { elapsed_ms: elapsed });
    }
    return true;
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

  function secretPassageDestination(snapshot) {
    const room = String(snapshot.legal_actions?.current_room || "").trim().toLowerCase();
    const target = snapshot.secret_passages?.[room];
    return target ? titleize(target) : "";
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
    const started = nowMs();
    const response = await fetch(path, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        "X-Clue-Seat-Token": seatToken,
        ...(options.headers || {}),
      },
    });
    const payload = await response.json().catch(() => ({}));
    clientTelemetry.lastFetchMs = roundedMs(nowMs() - started);
    app.dataset.snapshotFetchMs = String(clientTelemetry.lastFetchMs);
    if (!response.ok) {
      throw new Error(payload.error || `Request failed (${response.status}).`);
    }
    clearError();
    return payload;
  }

  function prettifyPhase(phase) {
    return titleize(phase);
  }

  function nearTop(container, threshold = 42) {
    return container.scrollTop <= threshold;
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

  function stagedMoveTarget(snapshot) {
    const value = String(actionDrafts.get("move-target") || "").trim();
    if (!value) {
      return "";
    }
    return (snapshot.legal_actions?.move_targets || []).some((item) => item.node_id === value) ? value : "";
  }

  function boardViewBox(snapshot) {
    const nodes = snapshot.board_nodes || [];
    if (!nodes.length) {
      return { minX: 0, minY: 0, width: 720, height: 560 };
    }

    let minX = Infinity;
    let maxX = -Infinity;
    let minY = Infinity;
    let maxY = -Infinity;
    nodes.forEach((node) => {
      const halfWidth = node.kind === "room" ? 88 : 30;
      const above = node.kind === "room" ? 56 : 24;
      const below = node.kind === "room" ? 68 : 58;
      minX = Math.min(minX, node.x - halfWidth);
      maxX = Math.max(maxX, node.x + halfWidth);
      minY = Math.min(minY, node.y - above);
      maxY = Math.max(maxY, node.y + below);
    });
    const margin = 18;
    return {
      minX: Math.floor(minX - margin),
      minY: Math.floor(minY - margin),
      width: Math.ceil((maxX - minX) + (margin * 2)),
      height: Math.ceil((maxY - minY) + (margin * 2)),
    };
  }

  async function submitBoardMove(nodeId) {
    if (pendingMutation || !nodeId) {
      return;
    }
    actionDrafts.set("move-target", nodeId);
    if (currentSnapshot) {
      renderBoard(currentSnapshot);
    }
    await submitMutation("action", "api/v1/games/current/actions", {
      action: "move",
      target_node: nodeId,
    });
  }

  function movementEdgeTarget(edge, highlights, nodesById) {
    const fromHighlighted = highlights.has(edge.from);
    const toHighlighted = highlights.has(edge.to);
    if (!fromHighlighted && !toHighlighted) {
      return "";
    }
    if (fromHighlighted && !toHighlighted) {
      return edge.from;
    }
    if (toHighlighted && !fromHighlighted) {
      return edge.to;
    }
    const from = nodesById.get(edge.from);
    const to = nodesById.get(edge.to);
    if (from?.kind === "room" && to?.kind !== "room") {
      return edge.from;
    }
    if (to?.kind === "room" && from?.kind !== "room") {
      return edge.to;
    }
    return "";
  }

  function bindBoardMoveControl(node, nodeId, label, options = {}) {
    const focusable = options.focusable !== false;
    node.dataset.boardTarget = nodeId;
    if (!focusable) {
      node.setAttribute("aria-hidden", "true");
      return;
    }
    node.setAttribute("role", "button");
    node.setAttribute("tabindex", pendingMutation ? "-1" : "0");
    node.setAttribute("aria-label", `Move to ${label}`);
    if (pendingMutation) {
      node.setAttribute("aria-disabled", "true");
      return;
    }
    node.setAttribute("aria-disabled", "false");
  }

  function boardMoveTargetFromEvent(event) {
    const rawTarget = event.target;
    if (!rawTarget || typeof rawTarget.closest !== "function") {
      return "";
    }
    const target = rawTarget.closest("[data-board-target]");
    if (!target || !board.contains(target) || target.getAttribute("aria-disabled") === "true") {
      return "";
    }
    return String(target.dataset.boardTarget || "");
  }

  function renderBoard(snapshot) {
    const moveTargets = snapshot.legal_actions.move_targets || [];
    const highlights = new Set(moveTargets.map((item) => item.node_id));
    const moveModes = new Map(moveTargets.map((item) => [item.node_id, item.mode || "walk"]));
    const stagedMove = stagedMoveTarget(snapshot);
    const seatsById = seatMap(snapshot);
    const nodesById = boardNodeById(snapshot);
    const seatPositions = {};
    const surface = document.createElementNS("http://www.w3.org/2000/svg", "g");
    const edgeLayer = document.createElementNS("http://www.w3.org/2000/svg", "g");
    const nodeLayer = document.createElementNS("http://www.w3.org/2000/svg", "g");
    const hitLayer = document.createElementNS("http://www.w3.org/2000/svg", "g");
    const viewBox = boardViewBox(snapshot);

    edgeLayer.setAttribute("class", "board-edge-layer");
    nodeLayer.setAttribute("class", "board-node-layer");
    hitLayer.setAttribute("class", "board-hit-layer");

    snapshot.seats.forEach((seat) => {
      const position = seat.seat_id === snapshot.seat.seat_id && stagedMove ? stagedMove : seat.position;
      if (!seatPositions[position]) {
        seatPositions[position] = [];
      }
      seatPositions[position].push(seatsById.get(seat.seat_id));
    });

    board.innerHTML = "";
    board.dataset.hasMoves = highlights.size ? "1" : "0";
    board.setAttribute("viewBox", `${viewBox.minX} ${viewBox.minY} ${viewBox.width} ${viewBox.height}`);
    (snapshot.board_edges || []).forEach((edge) => {
      const from = nodesById.get(edge.from);
      const to = nodesById.get(edge.to);
      if (!from || !to) {
        return;
      }
      const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
      const edgeClasses = ["board-edge", `board-edge-${edge.kind || "walk"}`];
      const edgeTarget = movementEdgeTarget(edge, highlights, nodesById);
      if (edgeTarget) {
        edgeClasses.push("board-edge-reachable");
        edgeClasses.push("clickable-edge");
        bindBoardMoveControl(line, edgeTarget, nodesById.get(edgeTarget)?.label || edgeTarget);
      }
      line.setAttribute("class", edgeClasses.join(" "));
      line.setAttribute("x1", from.x);
      line.setAttribute("y1", from.y);
      line.setAttribute("x2", to.x);
      line.setAttribute("y2", to.y);
      edgeLayer.appendChild(line);
      if (edgeTarget) {
        const hitLine = document.createElementNS("http://www.w3.org/2000/svg", "line");
        hitLine.setAttribute("class", "edge-hit-area");
        hitLine.setAttribute("x1", from.x);
        hitLine.setAttribute("y1", from.y);
        hitLine.setAttribute("x2", to.x);
        hitLine.setAttribute("y2", to.y);
        bindBoardMoveControl(hitLine, edgeTarget, nodesById.get(edgeTarget)?.label || edgeTarget, { focusable: false });
        hitLayer.appendChild(hitLine);
      }
    });
    snapshot.board_nodes.forEach((node) => {
      const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
      const nodeClasses = [`node`, node.kind];
      const highlightMode = moveModes.get(node.id) || "";
      if (highlights.has(node.id)) {
        nodeClasses.push("highlight");
        nodeClasses.push(highlightMode === "passage" ? "highlight-passage" : "highlight-walk");
      }
      if (stagedMove && stagedMove === node.id) {
        nodeClasses.push("selected-target");
      }
      if ((stagedMove || snapshot.seat.position) === node.id) {
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
        let hitArea = null;
        if (node.kind === "room") {
          hitArea = document.createElementNS("http://www.w3.org/2000/svg", "rect");
          hitArea.setAttribute("class", "node-hit-area");
          hitArea.setAttribute("x", node.x - 76);
          hitArea.setAttribute("y", node.y - 48);
          hitArea.setAttribute("width", 152);
          hitArea.setAttribute("height", 96);
          hitArea.setAttribute("rx", 18);
        } else {
          hitArea = document.createElementNS("http://www.w3.org/2000/svg", "circle");
          hitArea.setAttribute("class", "node-hit-area");
          hitArea.setAttribute("cx", node.x);
          hitArea.setAttribute("cy", node.y);
          hitArea.setAttribute("r", node.kind === "hallway" ? 38 : 30);
        }
        bindBoardMoveControl(g, node.id, node.label);
        bindBoardMoveControl(hitArea, node.id, node.label, { focusable: false });
        hitLayer.appendChild(hitArea);
      }
      nodeLayer.appendChild(g);
    });
    surface.appendChild(edgeLayer);
    surface.appendChild(nodeLayer);
    surface.appendChild(hitLayer);
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
    if (event.visibility === "public") {
      if (isPlayerMode() && !PLAYER_NARRATIVE_TYPES.has(String(event.event_type || ""))) {
        return "ignore";
      }
      return "narrative";
    }
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
      llm_unavailable: "LLM Stopped",
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

  function prependEventItems(container, snapshot, events, { chatChannel = false } = {}) {
    if (!events.length) return;
    const shouldStick = forceChatScroll || nearTop(container);
    const emptyState = container.querySelector(".empty-state");
    if (emptyState) {
      emptyState.remove();
    }
    const previousHeight = container.scrollHeight;
    const fragment = document.createDocumentFragment();
    events.forEach((event) => {
      fragment.appendChild(renderEventItem(snapshot, event));
    });
    container.prepend(fragment);
    if (chatChannel && !shouldStick) {
      chatUnread += events.length;
    }
    if (shouldStick) {
      container.scrollTop = 0;
      if (chatChannel) {
        chatUnread = 0;
      }
      return;
    }
    container.scrollTop += container.scrollHeight - previousHeight;
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
      eventsByChannel[channel].unshift(event);
      appended[channel].unshift(event);
    });
    return appended;
  }

  function renderEventPanels(snapshot, appended) {
    prependEventItems(narrativeLog, snapshot, appended.narrative);
    prependEventItems(chatLog, snapshot, appended.chat, { chatChannel: true });
    prependEventItems(privateLog, snapshot, appended.private);

    ensureEmptyState(
      narrativeLog,
      eventsByChannel.narrative,
      isPlayerMode() ? "Suggestions, refutations, and accusations will appear here." : "The public story of the game will appear here.",
    );
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
    applyCollapsibleState(privateIntelDrawer, eventsByChannel.private.length > 0);
  }

  function actionPriorityText(snapshot) {
    const available = new Set(deskActions(snapshot));
    const currentRoom = snapshot.legal_actions?.current_room;
    const passageTo = secretPassageDestination(snapshot);

    if (snapshot.status === "complete") {
      return "Review the finish, your notes, and the final evidence trail.";
    }
    if (currentSeatIsRefuting(snapshot)) {
      return available.has("show_refute_card")
        ? "Choose one legal card to reveal privately."
        : "Pass. No legal refute card is available.";
    }
    if (snapshot.active_seat_id !== snapshot.seat.seat_id) {
      return available.size
        ? `Waiting for your next prompt. Off-turn options: ${[...available].map(actionLabel).join(", ")}.`
        : "This desk is quiet until your seat is prompted again.";
    }
    if (available.has("roll")) {
      return currentRoom && passageTo
        ? `Choose between rolling and the passage from ${currentRoom} to ${passageTo}.`
        : "Roll to open movement.";
    }
    if (available.has("move")) {
      return isPlayerMode() ? "Move by clicking a highlighted board space." : "Click a highlighted board space, or use the movement controls.";
    }
    if (available.has("suggest")) {
      return currentRoom
        ? `Pressure the ${currentRoom} before you consider ending the turn.`
        : "Use the current room to make a suggestion.";
    }
    if (available.has("end_turn") && available.has("accuse")) {
      return "End the turn unless your evidence supports a final call.";
    }
    if (available.has("accuse")) {
      return "Accuse only if your evidence is decisive.";
    }
    if (available.has("end_turn")) {
      return "Close the turn once your review is complete.";
    }
    return "No immediate table action is queued for this seat.";
  }

  function turnRailEntries(snapshot) {
    const legal = snapshot.legal_actions || {};
    const currentRoom = legal.current_room;
    const phase = snapshot.phase;
    const available = new Set(deskActions(snapshot));

    if (snapshot.status === "complete") {
      return [
        { label: "Open", note: "Done", state: "done" },
        { label: "Move", note: "Done", state: "done" },
        { label: "Room Play", note: "Done", state: "done" },
        { label: "Close", note: "Solved", state: "done" },
      ];
    }

    if (currentSeatIsRefuting(snapshot)) {
      return [
        { label: "Suggest", note: legal.pending_refute?.suggestion?.room || "Public", state: "done" },
        { label: "Refute", note: available.has("show_refute_card") ? "Choose card" : "Pass only", state: "current" },
        { label: "Resume", note: "After refute", state: "upcoming" },
      ];
    }

    const entries = [
      { label: "Open", note: available.has("roll") ? "Roll / Passage" : snapshot.current_roll ? `Rolled ${snapshot.current_roll}` : "Ready", state: "waiting" },
      { label: "Move", note: available.has("move") ? `${(legal.move_targets || []).length} targets` : currentRoom || "Board", state: "waiting" },
      { label: "Room Play", note: currentRoom || "Need room", state: "waiting" },
      { label: "Close", note: available.has("end_turn") ? "Ready" : "Later", state: "waiting" },
    ];

    if (phase === "start_turn") {
      entries[0].state = "current";
      entries[1].state = "upcoming";
      entries[2].state = currentRoom ? "ready" : "waiting";
      entries[3].state = available.has("end_turn") ? "ready" : "waiting";
    } else if (phase === "move") {
      entries[0].state = "done";
      entries[1].state = "current";
      entries[2].state = currentRoom ? "ready" : "waiting";
      entries[3].state = available.has("end_turn") ? "ready" : "waiting";
    } else if (phase === "post_move") {
      entries[0].state = "done";
      entries[1].state = "done";
      entries[2].state = available.has("suggest") ? "current" : currentRoom ? "ready" : "waiting";
      entries[3].state = available.has("end_turn") ? "ready" : "waiting";
    } else if (phase === "post_suggest") {
      entries[0].state = "done";
      entries[1].state = "done";
      entries[2].state = "done";
      entries[3].state = available.has("end_turn") ? "current" : "ready";
    } else {
      entries[0].state = snapshot.current_roll ? "done" : "waiting";
      entries[1].state = available.has("move") ? "current" : currentRoom ? "done" : "waiting";
      entries[2].state = available.has("suggest") ? "current" : currentRoom ? "ready" : "waiting";
      entries[3].state = available.has("end_turn") ? "ready" : "waiting";
    }

    return entries;
  }

  function renderTurnRail(snapshot) {
    turnRail.innerHTML = turnRailEntries(snapshot).map((item, index) => `
      <li class="rail-step" data-state="${escapeHtml(item.state)}">
        <span class="rail-index">${index + 1}</span>
        <div class="rail-copy">
          <strong>${escapeHtml(item.label)}</strong>
          <span>${escapeHtml(item.note)}</span>
        </div>
      </li>
    `).join("");
  }

  function renderGuidance(snapshot) {
    const available = new Set(snapshot.legal_actions?.available || []);
    const activeSeat = seatMap(snapshot).get(snapshot.active_seat_id);
    const activeName = seatName(snapshot, snapshot.active_seat_id);
    const state = currentViewState(snapshot);
    const winnerName = snapshot.winner_seat_id ? seatName(snapshot, snapshot.winner_seat_id) : "Unknown";
    const playerMode = isPlayerMode();

    setState(turnBanner, state);
    setState(phasePill, state);
    setState(actionStatus, state);
    setState(decisionContextCard, state);
    setState(actionPanel, state);
    activeSeatLabel.textContent = snapshot.status === "complete" ? winnerName : activeName;
    turnIndexLabel.textContent = `Turn ${Number(snapshot.turn_index || 0) + 1}`;
    legalCount.textContent = snapshot.status === "complete" ? "Closed" : String(deskActions(snapshot).length);
    actionPriorityNote.textContent = actionPriorityText(snapshot);
    renderTurnRail(snapshot);

    if (snapshot.status === "complete") {
      actionStatus.textContent = "Case Closed";
      decisionContext.textContent = `${winnerName} solved the case.`;
      turnBanner.textContent = `Case closed. Winner: ${winnerName}`;
      turnGuidance.textContent = playerMode
        ? "Case closed."
        : snapshot.winner_seat_id
        ? "Review the wire, your notes, and the private intel feed."
        : "The case is closed.";
      return;
    }
    if (available.has("show_refute_card") || available.has("pass_refute")) {
      actionStatus.textContent = "Private Refute";
      decisionContext.textContent = playerMode ? "Refute." : "Choose one private refute response.";
      turnBanner.textContent = playerMode ? `${snapshot.seat.display_name}: refute` : `${snapshot.seat.display_name}, resolve the private refute.`;
      turnGuidance.textContent = playerMode ? "Show a card or pass." : "Any shown card stays private to the suggesting seat.";
      return;
    }
    if (snapshot.active_seat_id === snapshot.seat.seat_id) {
      actionStatus.textContent = "Your Turn";
      decisionContext.textContent = playerMode ? "Your turn." : `Turn ${Number(snapshot.turn_index || 0) + 1} is yours to resolve.`;
      turnBanner.textContent = playerMode ? `${snapshot.seat.display_name}: your turn` : `${snapshot.seat.display_name}, you are up.`;
      if (available.has("roll")) {
        turnGuidance.textContent = playerMode ? "Roll or use a passage." : "Roll now, or use a passage if one is available.";
      } else if (available.has("move")) {
        turnGuidance.textContent = playerMode
          ? "Click a highlighted target."
          : "Click a lit target to move now, or use the selector.";
      } else if (available.has("suggest")) {
        turnGuidance.textContent = playerMode ? "Make a suggestion." : "Make one room suggestion before you end the turn.";
      } else if (available.has("accuse")) {
        turnGuidance.textContent = playerMode ? "Accuse only if ready." : "Only open Final Call if your evidence is strong.";
      } else {
        turnGuidance.textContent = playerMode ? "End the turn when ready." : "Review the table, then finish the turn when ready.";
      }
      return;
    }
    if (activeSeat && activeSeat.seat_kind !== "human") {
      actionStatus.textContent = "AI Seat Acting";
      decisionContext.textContent = playerMode ? `Waiting on ${activeName}.` : `${activeName} is acting.`;
      turnBanner.textContent = playerMode ? `Waiting on ${activeName}` : `${activeName} is resolving an autonomous turn.`;
      turnGuidance.textContent = playerMode ? "Waiting." : "Live updates will catch up automatically.";
      return;
    }
    actionStatus.textContent = "Waiting";
    decisionContext.textContent = playerMode ? `Waiting on ${activeName}.` : `${activeName} currently controls the table.`;
    turnBanner.textContent = playerMode ? `Waiting on ${activeName}` : `Waiting on ${activeName}.`;
    turnGuidance.textContent = playerMode
      ? "Waiting."
      : activeSeat
      ? "Your private areas stay live while you wait."
      : "Waiting on the next seat.";
  }

  function latestLlmFailureMetric(snapshot) {
    const metrics = snapshot.analysis?.recent_turn_metrics || [];
    return [...metrics].reverse().find((item) => {
      return item?.action === "llm_unavailable" || item?.rejection_kind === "llm_unavailable";
    }) || null;
  }

  function latestLlmFailureEvent(snapshot) {
    const events = [
      ...(snapshot.events || []),
      ...eventsByChannel.narrative,
      ...eventsByChannel.private,
    ];
    return [...events].reverse().find((event) => {
      return event?.event_type === "llm_unavailable";
    }) || null;
  }

  function latestLlmFailure(snapshot) {
    const metric = latestLlmFailureMetric(snapshot);
    const event = latestLlmFailureEvent(snapshot);
    if (!metric && !event) {
      return null;
    }
    const seatId = metric?.seat_id || event?.payload?.seat_id || "";
    const fallbackUsed = Boolean(metric?.fallback_used);
    return {
      seatId,
      actorName: seatId ? seatName(snapshot, seatId) : "LLM seat",
      reason: metric?.llm_error_reason || event?.payload?.reason || "unavailable",
      mode: event?.payload?.mode || "",
      action: metric?.action || event?.event_type || "llm_unavailable",
      latencyMs: metric?.latency_ms ?? "",
      decisionLatencyMs: metric?.agent_decision_latency_ms ?? "",
      fallbackUsed,
      message: event?.message || "An LLM seat stopped instead of using a heuristic move.",
    };
  }

  function renderSeatDebug(snapshot) {
    const debug = snapshot.analysis?.seat_debug || {};
    const metric = debug.metric || null;
    const toolSnapshot = debug.tool_snapshot || {};
    const topHypotheses = toolSnapshot.top_hypotheses || [];
    const topSuggestions = toolSnapshot.suggestion_ranking || [];
    const accusation = toolSnapshot.accusation || {};
    const failure = latestLlmFailure(snapshot);
    const hasPrivateFailureTrace = Boolean(metric && (metric.action === "llm_unavailable" || metric.rejection_kind === "llm_unavailable"));

    if (!metric && !topHypotheses.length && !topSuggestions.length && !failure) {
      debugStatus.textContent = "Idle";
      seatDebug.innerHTML = '<p class="empty-state">No private agent-debug payload has been recorded for this seat yet.</p>';
      return;
    }

    debugStatus.textContent = failure ? "LLM Failed" : (metric?.fallback_used ? "Fallback" : "Live");
    const failureBlock = failure ? `
      <div class="debug-block debug-alert" data-state="failure">
        <p class="card-kicker">Latest LLM Failure</p>
        <p>${escapeHtml(failure.message)}</p>
        <dl class="debug-kv">
          <div><dt>Seat</dt><dd>${escapeHtml(failure.actorName)}</dd></div>
          <div><dt>Reason</dt><dd>${escapeHtml(failure.reason)}</dd></div>
          <div><dt>Last Action</dt><dd>${escapeHtml(failure.action)}</dd></div>
          <div><dt>Total Latency</dt><dd>${escapeHtml(failure.latencyMs || "--")} ms</dd></div>
          <div><dt>Model Latency</dt><dd>${escapeHtml(failure.decisionLatencyMs || "--")} ms</dd></div>
          <div><dt>Fallback</dt><dd>${failure.fallbackUsed ? "Used" : "Stopped; no heuristic move"}</dd></div>
        </dl>
      </div>
    ` : "";
    const privateTraceBlock = hasPrivateFailureTrace ? `
      <div class="debug-block">
        <p class="card-kicker">Private Failure Trace</p>
        <p>${escapeHtml(debug.decision_debug?.error || "Private runtime diagnostics were recorded for this affected seat.")}</p>
        <dl class="debug-kv">
          <div><dt>Runtime</dt><dd>${escapeHtml(debug.decision_debug?.llm_runtime?.sdk_backend || "--")}</dd></div>
          <div><dt>Model</dt><dd>${escapeHtml(metric?.model || debug.decision_debug?.llm_runtime?.default_model || "--")}</dd></div>
          <div><dt>Reasoning</dt><dd>${escapeHtml(metric?.reasoning_effort || "--")}</dd></div>
          <div><dt>Guardrails</dt><dd>${escapeHtml(metric?.guardrail_blocks ?? 0)}</dd></div>
        </dl>
      </div>
    ` : failure ? `
      <div class="debug-block">
        <p class="card-kicker">Trace Scope</p>
        <p>Detailed failure diagnostics are private to the affected seat and Superplayer Admin.</p>
      </div>
    ` : "";
    seatDebug.innerHTML = `
      ${failureBlock}
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
      ${privateTraceBlock}
    `;
  }

  function renderAiExplainer(snapshot) {
    const metrics = snapshot.analysis?.game_metrics || {};
    const targets = snapshot.analysis?.latency_targets_ms || {};
    aiExplainer.innerHTML = `
      <p class="field-note">LLM seats get a private deduction snapshot and choose one legal action with structured output. If the live model path is unavailable or invalid, the turn is recorded as an LLM failure instead of using a heuristic move.</p>
      <div class="debug-grid">
        <article class="summary-stat">
          <span class="card-kicker">LLM Failures</span>
          <strong>${escapeHtml(metrics.llm_unavailable_count ?? 0)}</strong>
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
    const chatLength = chatInput.value.trim().length;
    const notebookLength = notebookText.value.length;
    notebookStatus.textContent = notebookDirty ? "Unsaved" : "Synced";
    notebookStatus.dataset.state = notebookDirty ? "dirty" : "synced";
    if (pendingMutation === "notebook") {
      notebookDraftState.textContent = "Saving seat notebook...";
      notebookDraftState.dataset.state = "busy";
    } else if (notebookDirty) {
      notebookDraftState.textContent = `${notebookLength} ${notebookLength === 1 ? "character" : "characters"} unsaved locally.`;
      notebookDraftState.dataset.state = "dirty";
    } else {
      notebookDraftState.textContent = "Seat notebook synced.";
      notebookDraftState.dataset.state = "synced";
    }
    if (pendingMutation === "chat") {
      chatDraftState.textContent = "Posting public chat...";
      chatDraftState.dataset.state = "busy";
    } else if (chatLength) {
      chatDraftState.textContent = `${chatLength} ${chatLength === 1 ? "character" : "characters"} ready. Draft survives refresh.`;
      chatDraftState.dataset.state = "ready";
    } else {
      chatDraftState.textContent = "Draft empty.";
      chatDraftState.dataset.state = "idle";
    }
    saveNotebook.disabled = !notebookDirty || busy;
    sendChat.disabled = !chatInput.value.trim() || busy;
    saveNotebook.textContent = pendingMutation === "notebook" ? "Saving..." : "Save Notes";
    sendChat.textContent = pendingMutation === "chat" ? "Posting..." : "Send Chat";
    board.dataset.busy = busy ? "1" : "0";
    board.querySelectorAll("[data-board-target]:not([aria-hidden='true'])").forEach((node) => {
      node.setAttribute("tabindex", busy ? "-1" : "0");
      node.setAttribute("aria-disabled", busy ? "true" : "false");
    });
    actionControls.querySelectorAll("button[data-action-button='1'], select").forEach((control) => {
      control.disabled = busy;
    });
  }

  function renderSummary(snapshot) {
    const renderStarted = nowMs();
    const maxEventIndex = Math.max(eventCursor, ...((snapshot.events || []).map((event) => Number(event.event_index || 0))));
    const nextUiMode = normalizeUiMode(snapshot.ui_mode);
    const nextSnapshotKey = [
      nextUiMode,
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
    applyUiMode(snapshot);
    gameTitle.textContent = snapshot.title;
    phasePill.textContent = prettifyPhase(snapshot.phase);
    paceNote.textContent = waitingOnAutonomousSeat(snapshot)
      ? "Fast updates are active while an autonomous seat resolves."
      : "Draft-safe live updates are active.";
    paceNote.title = `Last snapshot fetch: ${clientTelemetry.lastFetchMs} ms; last render: ${clientTelemetry.lastRenderMs} ms.`;

    renderIfChanged("seat-summary", {
      ui_mode: currentUiMode,
      status: snapshot.status,
      phase: snapshot.phase,
      active_seat_id: snapshot.active_seat_id,
      winner_seat_id: snapshot.winner_seat_id,
      seat: snapshot.seat,
    }, () => renderSeatSummary(snapshot));
    renderIfChanged("hand", snapshot.seat.hand, () => {
      handList.innerHTML = snapshot.seat.hand.map((card) => `<li>${escapeHtml(card)}</li>`).join("");
    });
    if (!notebookDirty) {
      notebookText.value = snapshot.notebook?.text || "";
    }

    const seatPositionFingerprint = {
      ui_mode: currentUiMode,
      active_seat_id: snapshot.active_seat_id,
      seats: snapshot.seats.map((seat) => ({
        seat_id: seat.seat_id,
        display_name: seat.display_name,
        character: seat.character,
        seat_kind: seat.seat_kind,
        position: seat.position,
        can_win: seat.can_win,
      })),
    };
    renderIfChanged("seat-cards", seatPositionFingerprint, () => renderSeatCards(snapshot));
    renderIfChanged("position-grid", seatPositionFingerprint, () => renderPositionGrid(snapshot));
    renderIfChanged("board", {
      ...seatPositionFingerprint,
      current_seat_id: snapshot.seat.seat_id,
      current_seat_position: snapshot.seat.position,
      move_targets: snapshot.legal_actions?.move_targets || [],
      staged_move: stagedMoveTarget(snapshot),
      board_nodes: snapshot.board_nodes,
      board_edges: snapshot.board_edges,
    }, () => renderBoard(snapshot));

    const appended = ingestEvents(snapshot.events || []);
    renderIfChanged("events", {
      ui_mode: currentUiMode,
      narrative_count: eventsByChannel.narrative.length,
      chat_count: eventsByChannel.chat.length,
      private_count: eventsByChannel.private.length,
      chat_unread: chatUnread,
      appended: [...appended.narrative, ...appended.chat, ...appended.private].map((event) => event.event_index),
    }, () => renderEventPanels(snapshot, appended));

    const nextLegalFingerprint = JSON.stringify({ ui_mode: currentUiMode, legal_actions: snapshot.legal_actions || {} });
    if (nextLegalFingerprint !== legalFingerprint) {
      legalFingerprint = nextLegalFingerprint;
      renderActions(snapshot);
    }
    renderIfChanged("guidance", {
      ui_mode: currentUiMode,
      status: snapshot.status,
      turn_index: snapshot.turn_index,
      phase: snapshot.phase,
      active_seat_id: snapshot.active_seat_id,
      winner_seat_id: snapshot.winner_seat_id,
      current_roll: snapshot.current_roll,
      legal_actions: snapshot.legal_actions || {},
      seat_id: snapshot.seat.seat_id,
      seat_name: snapshot.seat.display_name,
      seats: snapshot.seats.map((seat) => ({
        seat_id: seat.seat_id,
        display_name: seat.display_name,
        seat_kind: seat.seat_kind,
      })),
    }, () => renderGuidance(snapshot));
    renderIfChanged("seat-debug", {
      debug: snapshot.analysis?.seat_debug || {},
      latest_llm_failure_metric: latestLlmFailureMetric(snapshot),
      latest_llm_failure_event: latestLlmFailureEvent(snapshot),
    }, () => renderSeatDebug(snapshot));
    renderIfChanged("ai-explainer", {
      game_metrics: snapshot.analysis?.game_metrics || {},
      latency_targets_ms: snapshot.analysis?.latency_targets_ms || {},
    }, () => renderAiExplainer(snapshot));
    updateDraftControls();
    clientTelemetry.lastRenderMs = roundedMs(nowMs() - renderStarted);
    app.dataset.snapshotRenderMs = String(clientTelemetry.lastRenderMs);
    paceNote.title = `Last snapshot fetch: ${clientTelemetry.lastFetchMs} ms; last render: ${clientTelemetry.lastRenderMs} ms.`;
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
      if (id === "move-target" && currentSnapshot) {
        renderBoard(currentSnapshot);
      }
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

  function createActionDrawer({ collapseKey, eyebrow, title, detail, tone = "neutral", defaultOpen = false }) {
    const details = document.createElement("details");
    details.className = "action-drawer";
    details.dataset.collapseKey = collapseKey;
    details.dataset.defaultOpen = defaultOpen ? "1" : "0";

    const summary = document.createElement("summary");
    summary.className = "action-drawer-summary";
    summary.innerHTML = `
      <div class="drawer-head">
        <div>
          <p class="card-kicker">${escapeHtml(eyebrow)}</p>
          <h4>${escapeHtml(title)}</h4>
        </div>
        <span class="drawer-note">${escapeHtml(detail)}</span>
      </div>
    `;
    details.appendChild(summary);

    const body = document.createElement("div");
    body.className = "action-drawer-body";
    details.appendChild(body);

    applyCollapsibleState(details, defaultOpen);
    return { details, body };
  }

  function renderActions(snapshot) {
    // Action controls are rebuilt every refresh because legality changes by phase.
    const legal = snapshot.legal_actions || {};
    const available = new Set(legal.available || []);
    const playerBoardMove = isPlayerMode() && available.has("move") && (legal.move_targets || []).length;
    actionControls.innerHTML = "";

    if (playerBoardMove) {
      const targetCount = legal.move_targets.length;
      const { card, body } = createActionCard({
        eyebrow: "Movement",
        title: "Move On Board",
        detail: `${targetCount} ${targetCount === 1 ? "target" : "targets"} highlighted.`,
        tone: "primary",
      });
      body.innerHTML = '<p class="empty-state">Click a highlighted room or route.</p>';
      actionControls.appendChild(card);
    }
    if (available.has("roll")) {
      const { card, body } = createActionCard({
        eyebrow: "Open Turn",
        title: "Roll For Movement",
        detail: "Open movement.",
        tone: "primary",
      });
      addActionButton(body, "Roll", () => ({ action: "roll" }));
      actionControls.appendChild(card);
    }
    if (available.has("move") && (legal.move_targets || []).length && !isPlayerMode()) {
      const targetCount = legal.move_targets.length;
      const options = legal.move_targets.map((item) => ({ value: item.node_id, label: `${item.label} (${item.cost})` }));
      const { card, body } = createActionCard({
        eyebrow: "Movement",
        title: "Choose A Destination",
        detail: `${targetCount} ${targetCount === 1 ? "target" : "targets"} lit on the board.`,
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
        title: "Room Suggestion",
        detail: "Refutations stay private.",
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
        detail: "Only the suggesting seat sees the card.",
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
    if (available.has("end_turn") && !playerBoardMove) {
      const { card, body } = createActionCard({
        eyebrow: "Wrap Up",
        title: "Close The Turn",
        detail: "Use this after movement and room play are done.",
        tone: "secondary",
      });
      addActionButton(body, "End Turn", () => ({ action: "end_turn" }), "secondary-action");
      actionControls.appendChild(card);
    }
    if (available.has("accuse") && !playerBoardMove) {
      const suspects = snapshot.case_file_categories.suspect.map((item) => ({ value: item, label: item }));
      const weapons = snapshot.case_file_categories.weapon.map((item) => ({ value: item, label: item }));
      const rooms = snapshot.case_file_categories.room.map((item) => ({ value: item, label: item }));
      const { details, body } = createActionDrawer({
        collapseKey: "final-call",
        eyebrow: "Final Call",
        title: "Accusation",
        detail: "Highest-risk action.",
        tone: "danger",
        defaultOpen: false,
      });
      body.classList.add("action-card", "action-card--drawer");
      body.dataset.tone = "danger";
      body.appendChild(buildSelect("accuse-suspect", suspects, "Accuse Suspect"));
      body.appendChild(buildSelect("accuse-weapon", weapons, "Accuse Weapon"));
      body.appendChild(buildSelect("accuse-room", rooms, "Accuse Room"));
      addActionButton(body, "Accuse", () => ({
        action: "accuse",
        suspect: document.getElementById("accuse-suspect").value,
        weapon: document.getElementById("accuse-weapon").value,
        room: document.getElementById("accuse-room").value,
      }), "danger-action");
      actionControls.appendChild(details);
    }
    if (!actionControls.children.length) {
      actionControls.innerHTML = playerBoardMove
        ? '<p class="empty-state">Click a highlighted board space.</p>'
        : '<p class="empty-state">No private actions are available from this seat right now.</p>';
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
    if (nearTop(chatLog)) {
      chatUnread = 0;
      chatCount.textContent = String(eventsByChannel.chat.length);
      chatFeedCount.textContent = String(eventsByChannel.chat.length);
      chatCount.dataset.state = "calm";
      chatFeedCount.dataset.state = "calm";
    }
  });

  board.addEventListener("click", (event) => {
    const targetNode = boardMoveTargetFromEvent(event);
    if (!targetNode) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    submitBoardMove(targetNode);
  });

  board.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") {
      return;
    }
    const targetNode = boardMoveTargetFromEvent(event);
    if (!targetNode) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    submitBoardMove(targetNode);
  });

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      refresh();
    }
  });

  initCollapsibleSections();
  updateDraftControls();
  refresh();
}
