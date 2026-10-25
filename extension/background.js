// Content scripts send it a message; it calls the API and sends back a plain result.

const BACKEND_URL = "http://127.0.0.1:8000";

async function api(path, options) {
    let response;
    try {
        response = await fetch(BACKEND_URL + path, options);
    }
    catch {
        return {
            ok: false,
            error: {
                code: "backend_unreachable",
                message: "Can't reach the backend. Is it running on 127.0.0.1:8000?"
            },
        };
    }

    let body = null;
    try {
        body = await response.json();

    }
    catch {
        // not JSON: keep body null
    }

    if (response.ok) {
        return {
            ok: true,
            data: body
        }
    }

    // Our backend sends {"detail": {"code", "message"}}; other failures get a generic message.
    const detail = body && body.detail;
    const error =
        detail && detail.code
            ? detail
            : { code: "http_" + response.status, message: `Backend error (${response.status}).` };
    return { ok: false, error };

}

chrome.runtime.onMessage.addListener((message , sender , sendResponse)=>{
    if(message.type === "ingest"){
        api("/ingest", {
            method :"POST",
            headers :{"Content-Type" : "application/json"},
            body: JSON.stringify({video_id : message.video_id}),
        }).then(sendResponse);
        return true ; 
    }
    return false; 
});