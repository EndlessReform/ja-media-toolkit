document.addEventListener('htmx:configRequest', function(evt) {
    var detail = evt.detail || {};
    var path = detail.path || '';
    if (path.startsWith('/toggle') || path.startsWith('/pick')) {
        Object.assign(detail.parameters, Object.fromEntries(new URLSearchParams(window.location.search)));
    }
});

function togglePicker(qid) {
    var row = document.getElementById('picker-' + qid);
    if (row) {
        row.style.display = row.style.display === 'none' ? 'table-row' : 'none';
    }
}
