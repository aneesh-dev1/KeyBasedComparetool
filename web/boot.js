// Runs before first paint (not deferred). After an in-app restart, the startup loader covers only the
// content area so the sidebar stays in place; a fresh visit keeps the full branded loader.
try{if(sessionStorage.getItem('ds-soft-reload')){document.documentElement.classList.add('soft-reload');sessionStorage.removeItem('ds-soft-reload');}}catch(error){}
