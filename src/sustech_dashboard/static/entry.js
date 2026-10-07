// Invitation stays in the browser fragment and the protected POST body.
function showInvitation() {
  const invitation = new URLSearchParams(location.hash.slice(1)).get('invite');
  if (invitation) {
    document.getElementById('invitation').value = invitation;
    document.getElementById('activation').open = true;
    history.replaceState(null, '', location.pathname + location.search);
  }
}
window.addEventListener('hashchange', showInvitation);
showInvitation();
for(const form of document.querySelectorAll('form'))form.addEventListener('submit',()=>{
  if(!/Windows/.test(navigator.userAgent))return;
  const key=form.elements.desktop_key.value,csrf=form.elements.csrf.value;
  sessionStorage.setItem('campus-desktop-handoff',JSON.stringify({key,csrf,started:Date.now()}));
  // Use the existing login click to start the component; authorization is sent only after login succeeds.
  const launch=document.createElement('a');
  launch.href='sustech-campus://login#'+new URLSearchParams({server:location.origin+'/app',key});
  launch.click();
});
