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
