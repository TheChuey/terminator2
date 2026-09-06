// ==========================================
// api/api.js - THE ONLY FILE THAT TALKS TO THE SERVER
// ==========================================
// Every HTTP request in the app goes through this module. No other
// file is allowed to call fetch() directly.
//
// ENDPOINTS USED:
//   getModels()            -> GET  /api/models
//   getAgents()            -> GET  /api/agents
//   sendChat(...)          -> POST /api/chat
//   loadAppSettings()      -> GET  /api/settings
//   saveAppSettings(...)   -> POST /api/settings   (merges)
//   saveChatSession(...)   -> POST /api/chat-save  (writes .txt file)

// "" (default) -> requests go to the SAME origin serving this page.
const API_BASE_URL = "";

/**
 * Low-level fetch helper: JSON in/out, readable error messages.
 */
async function request(path, options = {}) {
    let response;

    try {
        response = await fetch(API_BASE_URL + path, {
            headers: { "Content-Type": "application/json" },
            ...options,
        });
    } catch (networkError) {
        throw new Error(
            `Cannot reach the server at "${API_BASE_URL || window.location.origin}". Is it running? (python server.py)`
        );
    }

    if (!response.ok) {
        throw new Error(`Server error ${response.status} for ${path}`);
    }

    return response.json();
}

/** Model list for dropdowns. Returns [] when no Ollama models. */
export async function getModels() {
    const data = await request("/api/models");
    return data.models || [];
}

/** All discovered agents. Returns [] when agent_library/ is empty. */
export async function getAgents() {
    const data = await request("/api/agents");
    return data.agents || [];
}

/**
 * Send one chat message and return the server's reply + the tracked session.
 *
 * The server now owns the conversation: it returns a session_id you must send
 * back on every later message so the same chat keeps its own start/middle/end.
 * Pass newChat=true (or leave session_id empty) to start a fresh chat, which
 * finalizes whatever chat was active before.
 */
export async function sendChat({ message, agentId = "", model = "", history = [], sessionId = "", title = "", newChat = false, rag = false }) {
    const data = await request("/api/chat", {
        method: "POST",
        body: JSON.stringify({
            message,
            model,
            agent_id: agentId,
            history,
            session_id: sessionId,
            title,
            new_chat: newChat,
            rag,
        }),
    });
    return { reply: data.reply, session_id: data.session_id, title: data.title };
}

/** Header rows for every saved chat + the active one, newest first. */
export async function listChats() {
    const data = await request("/api/chats");
    return data.chats || [];
}

/** One chat: its log row + the .txt content + parsed messages. */
export async function getChat(chatId) {
    return request(`/api/chats/${encodeURIComponent(chatId)}`);
}

/**
 * Finalize the active chat: writes its .txt (next version on a name collision)
 * and logs it. Safe to call even when nothing is active.
 * rag: optional bool override - commit this chat to the RAG memory store.
 */
export async function endChat({ title = "", rag = undefined } = {}) {
    const body = { title };
    if (typeof rag === "boolean") {
        body.rag = rag;
    }
    return request("/api/chats/end", {
        method: "POST",
        body: JSON.stringify(body),
    });
}

/** RAG store info: location + indexed chunk count. */
export async function ragStatus() {
    return request("/api/rag/status");
}

/** Re-index every saved transcript into the RAG store. */
export async function rebuildRag() {
    return request("/api/rag/rebuild", { method: "POST" });
}

/** Delete the RAG store (transcripts + chat records are untouched). */
export async function resetRag() {
    return request("/api/rag/reset", { method: "POST" });
}

/** Load the stored browser settings; {} when nothing saved yet. */
export async function loadAppSettings() {
    const data = await request("/api/settings");
    return data.settings || {};
}

/**
 * Load settings plus the meta flags from /api/settings.
 * Returns { settings, restartNeeded } where restartNeeded is true when the
 * stored path settings changed since the server started (restart required).
 */
export async function loadAppSettingsWithMeta() {
    const data = await request("/api/settings");
    return {
        settings: data.settings || {},
        restartNeeded: Boolean(data.restartNeeded),
    };
}

/** Merge partial settings into what's stored (the rest survives). */
export async function saveAppSettings(partialSettings) {
    const data = await request("/api/settings", {
        method: "POST",
        body: JSON.stringify(partialSettings),
    });
    return data.settings || {};
}

/**
 * Save a chat session as a nicely-formatted .txt file on the server.
 *
 * The transcript + a suggested file name + the configured output path
 * are all sent here and written by /api/chat-save.
 */
export async function saveChatSession({
    path = "",
    fileName = "",
    title = "",
    agentName = "",
    model = "",
    content = "",
}) {
    const result = await request("/api/chat-save", {
        method: "POST",
        body: JSON.stringify({ path, fileName, title, agentName, model, content }),
    });

    if (!result.saved) {
        throw new Error(result.error || "The server refused to save the chat.");
    }

    return result; // { saved, file }
}
