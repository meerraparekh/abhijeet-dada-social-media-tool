const state = { sessions: [], currentId: null, pollTimers: {} };

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch (e) {}
    throw new Error(msg);
  }
  const ct = res.headers.get("content-type") || "";
  return ct.includes("application/json") ? res.json() : null;
}

async function loadSessions() {
  state.sessions = await api("/api/sessions");
  renderSidebar();
}

function renderSidebar() {
  const el = document.getElementById("sessionList");
  el.innerHTML = "";
  for (const s of state.sessions) {
    const div = document.createElement("div");
    div.className = "session-item" + (s.id === state.currentId ? " active" : "");
    div.innerHTML = `<div>${escapeHtml(s.name)}</div><div class="status">${s.status}</div>`;
    div.onclick = () => selectSession(s.id);
    el.appendChild(div);
  }
}

async function selectSession(id) {
  state.currentId = id;
  renderSidebar();
  await renderMain();
}

async function renderMain() {
  const main = document.getElementById("main");
  if (!state.currentId) {
    main.innerHTML = `<div class="empty-state">Select or create a session on the left to get started.</div>`;
    return;
  }
  const session = await api(`/api/sessions/${state.currentId}`);

  main.innerHTML = `
    <div class="action-row">
      <h2 style="margin:0">${escapeHtml(session.name)}</h2>
      <span class="status-pill">${session.status}</span>
      <button class="secondary" id="deleteSessionBtn">Delete session</button>
    </div>
    <video id="player" controls src="/api/sessions/${session.id}/video"></video>
    <div class="action-row">
      <button id="transcribeBtn" ${session.transcript ? "disabled" : ""}>Transcribe</button>
      <button id="suggestBtn" ${!session.transcript || session.clips.length ? "disabled" : ""}>Suggest clips</button>
      ${session.transcript ? `<a href="/api/sessions/${session.id}/transcript.srt">Download transcript (.srt)</a>` : ""}
      <span id="jobNote" class="progress-note"></span>
    </div>
    <div id="clips"></div>
  `;

  document.getElementById("deleteSessionBtn").onclick = () => deleteSession(session.id);
  document.getElementById("transcribeBtn").onclick = () => runJob(`/api/sessions/${session.id}/transcribe`, "POST");
  document.getElementById("suggestBtn").onclick = () => runJob(`/api/sessions/${session.id}/suggest-clips`, "POST");

  renderClips(session);
}

async function deleteSession(id) {
  if (!confirm("Delete this session and all its clips?")) return;
  await api(`/api/sessions/${id}`, { method: "DELETE" });
  state.currentId = null;
  await loadSessions();
  await renderMain();
}

function renderClips(session) {
  const el = document.getElementById("clips");
  if (!session.clips.length) {
    el.innerHTML = session.transcript
      ? `<div class="empty-state">No clips yet - click "Suggest clips" above.</div>`
      : "";
    return;
  }
  el.innerHTML = "";
  for (const clip of session.clips) {
    el.appendChild(renderClipCard(session, clip));
  }
}

function renderClipCard(session, clip) {
  const card = document.createElement("div");
  card.className = "clip-card";

  const platformLabels = { youtube: "YouTube", instagram_reel: "Instagram Reel", twitter: "Twitter/X" };
  const platformCheckboxes = Object.keys(platformLabels)
    .map(
      (p) => `<label><input type="checkbox" class="platform-cb" value="${p}" ${clip.rendered_files[p] ? "checked" : ""}/> ${platformLabels[p]}${clip.rendered_files[p] ? " <span class='progress-note'>(rendered - re-check to redo)</span>" : ""}</label>`
    )
    .join("");

  const downloads = Object.entries(clip.rendered_files)
    .map(([p, path]) => `<a href="/api/sessions/${session.id}/clips/${clip.id}/download/${p}" download>${platformLabels[p] || p}</a>`)
    .join("");

  card.innerHTML = `
    <div class="row">
      <div class="times">
        <label>Start (s)</label>
        <input type="number" step="0.1" class="f-start" value="${clip.start_seconds.toFixed(1)}" />
        <button class="secondary set-from-player" data-target="start">Set from player</button>
      </div>
      <div class="times">
        <label>End (s)</label>
        <input type="number" step="0.1" class="f-end" value="${clip.end_seconds.toFixed(1)}" />
        <button class="secondary set-from-player" data-target="end">Set from player</button>
      </div>
      <div>
        <label>Status</label>
        <select class="f-status">
          ${["suggested", "claimed", "rendering", "done"].map((s) => `<option value="${s}" ${s === clip.status ? "selected" : ""}>${s}</option>`).join("")}
        </select>
      </div>
      <div>
        <label>Assignee</label>
        <input type="text" class="f-assignee" value="${escapeAttr(clip.assignee)}" placeholder="who's editing this" />
      </div>
    </div>
    <div class="hook">"${escapeHtml(clip.hook_hi)}"<br/>"${escapeHtml(clip.hook_en)}"</div>
    <div class="row">
      <div><label>YouTube title (Hindi)</label><input type="text" class="f-yt-title-hi" value="${escapeAttr(clip.youtube_title_hi)}" /></div>
      <div><label>YouTube title (English)</label><input type="text" class="f-yt-title-en" value="${escapeAttr(clip.youtube_title_en)}" /></div>
    </div>
    <div class="row">
      <div><label>Instagram caption (Hindi)</label><textarea class="f-ig-caption-hi">${escapeHtml(clip.instagram_caption_hi)}</textarea></div>
      <div><label>Instagram caption (English)</label><textarea class="f-ig-caption-en">${escapeHtml(clip.instagram_caption_en)}</textarea></div>
    </div>
    <div class="row">
      <div><label>Twitter/X text (Hindi)</label><textarea class="f-tw-text-hi">${escapeHtml(clip.twitter_text_hi)}</textarea></div>
      <div><label>Twitter/X text (English)</label><textarea class="f-tw-text-en">${escapeHtml(clip.twitter_text_en)}</textarea></div>
    </div>
    <div class="row">
      <div><label>Hashtags</label><input type="text" class="f-hashtags" value="${escapeAttr(clip.hashtags.join(", "))}" /></div>
    </div>
    <div class="row" style="align-items:center">
      <div class="platform-row">${platformCheckboxes}</div>
      <label><input type="checkbox" class="remove-silence-cb" checked /> Remove silence/gaps</label>
      <button class="render-btn">Render selected</button>
      <div class="download-links">${downloads}</div>
    </div>
    <div class="progress-note render-note"></div>
  `;

  const save = async (patch) => {
    await api(`/api/sessions/${session.id}/clips/${clip.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    });
  };

  card.querySelector(".f-start").onchange = (e) => save({ start_seconds: parseFloat(e.target.value) });
  card.querySelector(".f-end").onchange = (e) => save({ end_seconds: parseFloat(e.target.value) });
  card.querySelector(".f-status").onchange = (e) => save({ status: e.target.value });
  card.querySelector(".f-assignee").onchange = (e) => save({ assignee: e.target.value });
  card.querySelector(".f-yt-title-hi").onchange = (e) => save({ youtube_title_hi: e.target.value });
  card.querySelector(".f-yt-title-en").onchange = (e) => save({ youtube_title_en: e.target.value });
  card.querySelector(".f-ig-caption-hi").onchange = (e) => save({ instagram_caption_hi: e.target.value });
  card.querySelector(".f-ig-caption-en").onchange = (e) => save({ instagram_caption_en: e.target.value });
  card.querySelector(".f-tw-text-hi").onchange = (e) => save({ twitter_text_hi: e.target.value });
  card.querySelector(".f-tw-text-en").onchange = (e) => save({ twitter_text_en: e.target.value });
  card.querySelector(".f-hashtags").onchange = (e) =>
    save({ hashtags: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) });

  card.querySelectorAll(".set-from-player").forEach((btn) => {
    btn.onclick = () => {
      const player = document.getElementById("player");
      const field = btn.dataset.target === "start" ? card.querySelector(".f-start") : card.querySelector(".f-end");
      field.value = player.currentTime.toFixed(1);
      save({ [btn.dataset.target === "start" ? "start_seconds" : "end_seconds"]: player.currentTime });
    };
  });

  card.querySelector(".render-btn").onclick = async () => {
    const platforms = [...card.querySelectorAll(".platform-cb:checked")].map((c) => c.value);
    if (!platforms.length) { alert("Pick at least one platform"); return; }
    const removeSilence = card.querySelector(".remove-silence-cb").checked;
    const note = card.querySelector(".render-note");
    note.textContent = "Rendering...";
    const { job_id } = await api(`/api/sessions/${session.id}/clips/${clip.id}/render`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ platforms, remove_silence: removeSilence }),
    });
    pollJob(job_id, (job) => {
      note.textContent = job.state === "running" ? job.progress || "rendering..." : "";
      if (job.state === "done") renderMain();
      if (job.state === "error") note.innerHTML = `<span class="error-note">${escapeHtml(job.error)}</span>`;
    });
  };

  return card;
}

async function runJob(path, method) {
  const note = document.getElementById("jobNote");
  note.textContent = "starting...";
  const { job_id } = await api(path, { method });
  pollJob(job_id, (job) => {
    note.textContent = job.state === "running" ? job.progress || "working..." : "";
    if (job.state === "done") { loadSessions(); renderMain(); }
    if (job.state === "error") note.innerHTML = `<span class="error-note">${escapeHtml(job.error)}</span>`;
  });
}

function pollJob(jobId, onUpdate) {
  const tick = async () => {
    const job = await api(`/api/jobs/${jobId}`);
    onUpdate(job);
    if (job.state === "running") setTimeout(tick, 2000);
  };
  tick();
}

document.getElementById("newSessionForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = document.getElementById("newSessionName").value;
  const file = document.getElementById("newSessionFile").files[0];
  const progress = document.getElementById("uploadProgress");
  progress.textContent = "Uploading...";

  const params = new URLSearchParams({ name });
  const form = new FormData();
  form.append("file", file);

  const xhr = new XMLHttpRequest();
  xhr.open("POST", `/api/sessions?${params.toString()}`);
  xhr.upload.onprogress = (evt) => {
    if (evt.lengthComputable) {
      progress.textContent = `Uploading... ${Math.round((evt.loaded / evt.total) * 100)}%`;
    }
  };
  xhr.onload = async () => {
    progress.textContent = xhr.status < 300 ? "Done." : `Upload failed: ${xhr.responseText}`;
    if (xhr.status < 300) {
      const session = JSON.parse(xhr.responseText);
      e.target.reset();
      await loadSessions();
      await selectSession(session.id);
    }
  };
  xhr.onerror = () => { progress.textContent = "Upload failed."; };
  xhr.send(form);
});

function escapeHtml(str) {
  return (str || "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function escapeAttr(str) { return escapeHtml(str); }

loadSessions();
