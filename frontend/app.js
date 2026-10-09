const state = { sessions: [], currentId: null, previewAngleId: null };

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
  state.previewAngleId = null;
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
  const primary = session.angles.find((a) => a.is_primary);
  const previewAngleId = state.previewAngleId || (primary && primary.id);
  const hasEnglish = session.transcript && session.transcript.segments_en && session.transcript.segments_en.length > 0;

  main.innerHTML = `
    <div class="action-row">
      <h2 style="margin:0">${escapeHtml(session.name)}</h2>
      <span class="status-pill">${session.status}</span>
      <button class="secondary" id="deleteSessionBtn">Delete session</button>
    </div>
    <div class="action-row">
      <label style="margin:0">Preview angle:</label>
      <select id="angleSelect">
        ${session.angles.map((a) => `<option value="${a.id}" ${a.id === previewAngleId ? "selected" : ""}>${escapeHtml(a.label)}${a.is_primary ? " (primary)" : ""}</option>`).join("")}
      </select>
    </div>
    <video id="player" controls src="/api/sessions/${session.id}/video?angle_id=${previewAngleId}"></video>
    <div class="action-row" style="margin-top:4px">
      <span>Current time: <strong id="playerSeconds">0.0</strong>s (use this to fill in Start/End below - always on the primary angle's timeline)</span>
    </div>

    <div id="anglesSection"></div>

    <div class="action-row">
      <button id="transcribeBtn" ${session.transcript ? "disabled" : ""}>Transcribe</button>
      <button id="suggestBtn" ${!session.transcript ? "disabled" : ""}>
        ${session.clips.length ? "Re-suggest clips" : "Suggest clips"}
      </button>
      <button id="translateBtn" ${!session.transcript ? "disabled" : ""}>
        ${hasEnglish ? "Re-translate captions" : "Translate captions (English)"}
      </button>
      <button id="blogBtn" ${!session.transcript ? "disabled" : ""}>
        ${session.blog_posts.length ? "Re-generate blog posts" : "Generate blog posts"}
      </button>
      <span id="jobNote" class="progress-note"></span>
    </div>
    <div class="action-row">
      ${session.transcript ? `<a href="/api/sessions/${session.id}/transcript.srt">Hindi transcript (.srt)</a>` : ""}
      ${session.transcript ? `<a href="/api/sessions/${session.id}/transcript.docx">Hindi transcript (Word)</a>` : ""}
      ${hasEnglish ? `<a href="/api/sessions/${session.id}/transcript_en.srt">English transcript (.srt)</a>` : ""}
      ${hasEnglish ? `<a href="/api/sessions/${session.id}/transcript_en.docx">English transcript (Word)</a>` : ""}
      ${session.blog_posts.length ? `<a href="/api/sessions/${session.id}/blog_posts.docx">Blog posts (Word, ${session.blog_posts.length})</a>` : ""}
    </div>
    ${session.blog_posts.length ? `<div class="blog-posts-box">${session.blog_posts.map((p) => `<div class="blog-post-row"><strong>${escapeHtml(p.title)}</strong> <span class="progress-note">${escapeHtml(p.tags.join(", "))}</span></div>`).join("")}</div>` : ""}
    <div id="clips"></div>
  `;

  document.getElementById("deleteSessionBtn").onclick = () => deleteSession(session.id);
  document.getElementById("transcribeBtn").onclick = () => runJob(`/api/sessions/${session.id}/transcribe`, "POST");
  document.getElementById("suggestBtn").onclick = () => {
    if (session.clips.length && !confirm(`This replaces the existing ${session.clips.length} clip(s), including any assignee/status edits. Continue?`)) return;
    runJob(`/api/sessions/${session.id}/suggest-clips`, "POST");
  };
  document.getElementById("translateBtn").onclick = () => runJob(`/api/sessions/${session.id}/translate-captions`, "POST");
  document.getElementById("blogBtn").onclick = () => runJob(`/api/sessions/${session.id}/generate-blog-posts`, "POST");
  document.getElementById("angleSelect").onchange = (e) => {
    state.previewAngleId = e.target.value;
    document.getElementById("player").src = `/api/sessions/${session.id}/video?angle_id=${e.target.value}`;
  };

  const player = document.getElementById("player");
  const secondsLabel = document.getElementById("playerSeconds");
  const updateSecondsLabel = () => { secondsLabel.textContent = player.currentTime.toFixed(1); };
  player.addEventListener("timeupdate", updateSecondsLabel);
  player.addEventListener("seeking", updateSecondsLabel);

  renderAngles(session);
  renderClips(session);
}

async function deleteSession(id) {
  if (!confirm("Delete this session and all its clips?")) return;
  // The session being deleted here is always the one currently shown, so
  // its video is very likely still loaded in the player - on Windows the
  // server can't delete a file the browser still has an open connection
  // to, so release it first and give the disconnect a moment to land
  // server-side before issuing the delete.
  await unloadPlayer();
  try {
    await api(`/api/sessions/${id}`, { method: "DELETE" });
  } catch (err) {
    alert(`Couldn't delete this session: ${err.message}`);
    return;
  }
  state.currentId = null;
  await loadSessions();
  await renderMain();
}

async function unloadPlayer() {
  const player = document.getElementById("player");
  if (!player) return;
  player.pause();
  player.removeAttribute("src");
  player.load();
  await new Promise((resolve) => setTimeout(resolve, 300));
}

const SYNC_LABELS = {
  primary: "primary",
  pending: "syncing...",
  synced: "synced",
  failed: "sync failed",
};

function renderAngles(session) {
  const el = document.getElementById("anglesSection");
  const rows = session.angles
    .map((a) => {
      const badge = SYNC_LABELS[a.sync_status] || a.sync_status;
      const extra = a.sync_status === "synced" ? ` (offset ${a.offset_seconds.toFixed(2)}s)` : "";
      const retry = a.sync_status === "failed"
        ? `<button class="secondary resync-btn" data-angle="${a.id}">Retry sync</button>`
        : "";
      const del = !a.is_primary
        ? `<button class="secondary delete-angle-btn" data-angle="${a.id}">Remove</button>`
        : "";
      const appendForm = a.sync_status !== "pending"
        ? `<form class="append-footage-form" data-angle="${a.id}">
            <input type="file" class="append-footage-file" accept="video/*,.mp4,.mov,.m4v,.avi,.mkv,.wmv,.mts,.m2ts" multiple required />
            <button type="submit" class="secondary">Add more footage</button>
            <span class="progress-note append-footage-note"></span>
          </form>`
        : "";
      return `<div class="angle-row-wrap">
        <div class="angle-row">
          <strong>${escapeHtml(a.label)}</strong>
          <span class="status-pill">${badge}${extra}</span>
          ${a.sync_error ? `<span class="error-note">${escapeHtml(a.sync_error)}</span>` : ""}
          ${retry} ${del}
        </div>
        ${appendForm}
      </div>`;
    })
    .join("");

  el.innerHTML = `
    <div class="angles-box">
      <label style="font-size:12px;color:var(--muted)">Camera angles</label>
      ${rows}
      <form id="addAngleForm" class="angle-add-form">
        <input type="text" id="angleLabel" placeholder="label, e.g. 2x" required style="width:120px" />
        <input type="file" id="angleFile" accept="video/*,.mp4,.mov,.m4v,.avi,.mkv,.wmv,.mts,.m2ts" multiple required />
        <button type="submit" class="secondary">Add angle</button>
        <span id="angleUploadNote" class="progress-note"></span>
      </form>
      <div class="progress-note">If an angle's recording was split across files (camera stopped partway), select all of them together, oldest first - or use "Add more footage" below if you find a missing part later.</div>
    </div>
  `;

  el.querySelectorAll(".resync-btn").forEach((btn) => {
    btn.onclick = async () => {
      await api(`/api/sessions/${session.id}/angles/${btn.dataset.angle}/resync`, { method: "POST" });
      pollUntilAnglesSettled(session.id);
    };
  });
  el.querySelectorAll(".delete-angle-btn").forEach((btn) => {
    btn.onclick = async () => {
      if (!confirm("Remove this angle? Any clips already rendered from it stay on disk.")) return;
      const primary = session.angles.find((a) => a.is_primary);
      const currentPreviewId = state.previewAngleId || (primary && primary.id);
      if (btn.dataset.angle === currentPreviewId) {
        // Same reasoning as deleteSession: if this angle's video is the one
        // currently loaded in the player, the server can't delete its file
        // on Windows until that connection is released.
        await unloadPlayer();
      }
      try {
        await api(`/api/sessions/${session.id}/angles/${btn.dataset.angle}`, { method: "DELETE" });
      } catch (err) {
        alert(`Couldn't remove this angle: ${err.message}`);
        return;
      }
      renderMain();
    };
  });

  document.getElementById("addAngleForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const label = document.getElementById("angleLabel").value;
    const files = [...document.getElementById("angleFile").files];
    const note = document.getElementById("angleUploadNote");
    note.textContent = "Uploading...";
    const form = new FormData();
    for (const f of files) form.append("files", f);
    try {
      await api(`/api/sessions/${session.id}/angles?${new URLSearchParams({ label })}`, { method: "POST", body: form });
      note.textContent = "Uploaded, processing...";
      pollUntilAnglesSettled(session.id);
    } catch (err) {
      note.innerHTML = `<span class="error-note">${escapeHtml(err.message)}</span>`;
    }
  });

  el.querySelectorAll(".append-footage-form").forEach((form) => {
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const angleId = form.dataset.angle;
      const files = [...form.querySelector(".append-footage-file").files];
      const note = form.querySelector(".append-footage-note");
      note.textContent = "Uploading...";
      const body = new FormData();
      for (const f of files) body.append("files", f);
      try {
        await api(`/api/sessions/${session.id}/angles/${angleId}/append`, { method: "POST", body });
        note.textContent = "Uploaded, re-processing...";
        pollUntilAnglesSettled(session.id);
      } catch (err) {
        note.innerHTML = `<span class="error-note">${escapeHtml(err.message)}</span>`;
      }
    });
  });
}

function pollUntilAnglesSettled(sessionId) {
  const tick = async () => {
    const session = await api(`/api/sessions/${sessionId}`);
    if (state.currentId !== sessionId) return;
    const stillPending = session.angles.some((a) => a.sync_status === "pending");
    if (stillPending) {
      // Partial refresh while waiting, so the angle-add form isn't blown away
      // mid-upload by a full re-render.
      renderAngles(session);
      renderClips(session);
      setTimeout(tick, 2000);
    } else {
      // Once settled, do a full re-render so the preview angle dropdown
      // (built in renderMain, not renderAngles) picks up the new angle too.
      renderMain();
    }
  };
  tick();
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

  const angleCheckboxes = session.angles
    .map((a) => {
      const canRender = a.sync_status === "primary" || a.sync_status === "synced";
      const rendered = clip.rendered_files[a.id];
      const note = !canRender ? ` <span class="progress-note">(${SYNC_LABELS[a.sync_status]})</span>` : rendered ? " <span class='progress-note'>(rendered - re-check to redo)</span>" : "";
      return `<label><input type="checkbox" class="angle-cb" value="${a.id}" ${rendered ? "checked" : ""} ${canRender ? "" : "disabled"}/> ${escapeHtml(a.label)}${note}</label>`;
    })
    .join("");

  const downloads = Object.entries(clip.rendered_files)
    .map(([angleId, path]) => {
      const angle = session.angles.find((a) => a.id === angleId);
      return `<a href="/api/sessions/${session.id}/clips/${clip.id}/download/${angleId}" download>${escapeHtml(angle ? angle.label : angleId)}</a>`;
    })
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
      <div class="times">
        <label>Hook start (s)</label>
        <input type="number" step="0.1" class="f-hook-start" value="${(clip.hook_start_seconds ?? clip.start_seconds).toFixed(1)}" />
        <button class="secondary set-from-player" data-target="hookStart">Set from player</button>
      </div>
      <div class="times">
        <label>Hook end (s)</label>
        <input type="number" step="0.1" class="f-hook-end" value="${(clip.hook_end_seconds ?? clip.start_seconds).toFixed(1)}" />
        <button class="secondary set-from-player" data-target="hookEnd">Set from player</button>
      </div>
    </div>
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
      <div><label>Tags (for blog posts)</label><input type="text" class="f-tags" value="${escapeAttr(clip.tags.join(", "))}" /></div>
    </div>
    <div class="row" style="align-items:center">
      <div class="platform-row">${angleCheckboxes}</div>
      <label><input type="checkbox" class="remove-silence-cb" checked /> Remove silence/gaps</label>
      <label><input type="checkbox" class="remove-fillers-cb" /> Remove filler words &amp; mistakes (AI, small extra cost)</label>
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
  card.querySelector(".f-hook-start").onchange = (e) => save({ hook_start_seconds: parseFloat(e.target.value) });
  card.querySelector(".f-hook-end").onchange = (e) => save({ hook_end_seconds: parseFloat(e.target.value) });
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
  card.querySelector(".f-tags").onchange = (e) =>
    save({ tags: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) });

  const TIME_TARGETS = {
    start: { selector: ".f-start", field: "start_seconds" },
    end: { selector: ".f-end", field: "end_seconds" },
    hookStart: { selector: ".f-hook-start", field: "hook_start_seconds" },
    hookEnd: { selector: ".f-hook-end", field: "hook_end_seconds" },
  };
  card.querySelectorAll(".set-from-player").forEach((btn) => {
    btn.onclick = () => {
      const player = document.getElementById("player");
      const { selector, field } = TIME_TARGETS[btn.dataset.target];
      card.querySelector(selector).value = player.currentTime.toFixed(1);
      save({ [field]: player.currentTime });
    };
  });

  card.querySelector(".render-btn").onclick = async () => {
    const angleIds = [...card.querySelectorAll(".angle-cb:checked")].map((c) => c.value);
    if (!angleIds.length) { alert("Pick at least one angle"); return; }
    const removeSilence = card.querySelector(".remove-silence-cb").checked;
    const removeFillers = card.querySelector(".remove-fillers-cb").checked;
    const note = card.querySelector(".render-note");
    note.textContent = "Rendering...";
    const { job_id } = await api(`/api/sessions/${session.id}/clips/${clip.id}/render`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ angle_ids: angleIds, remove_silence: removeSilence, remove_fillers: removeFillers }),
    });
    pollJob(job_id, (job) => {
      note.textContent = job.state === "running" ? job.progress || "rendering..." : job.progress || "";
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
  const files = [...document.getElementById("newSessionFile").files];
  const progress = document.getElementById("uploadProgress");
  progress.textContent = "Uploading...";

  const params = new URLSearchParams({ name });
  const form = new FormData();
  for (const f of files) form.append("files", f);

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
