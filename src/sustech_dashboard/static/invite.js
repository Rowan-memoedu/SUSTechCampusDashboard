(() => {const token = new URLSearchParams(location.hash.slice(1)).get('invite');history.replaceState(null, '', location.pathname);if (token) document.getElementById('invitation').value = token;})();
