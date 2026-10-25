
let currentVideoId  = null ; 

function getVideoId(){
    const url = new URL(location.href) ;
    if(url.pathname !== "/watch"){
        // home page , search , shorts , --- are ignored for now
        return null ; 
    }
    return url.searchParams.get("v");
}

function onVideoChange(videoId){
    console.log("[YT ChatBot] loaded. video id :", videoId);
}

// check the video is changed or not 
function checkVideo(){
    const id = getVideoId() ;
    if(id == currentVideoId){
        return ; // ignore the duplicate signals 
    }
    currentVideoId = id ; 
    onVideoChange(id) ;
}

// step 1) YouTube fires this on `document` every time its in-page navigation finishes.
document.addEventListener("yt-navigate-finish" , checkVideo);
// Step 2) Safety net in case Youtube renames or skips that event : a cheap URL check once a second 
setInterval(checkVideo , 1000) ;
// Step 3) Th page we landed on at load time 
checkVideo() ; 


