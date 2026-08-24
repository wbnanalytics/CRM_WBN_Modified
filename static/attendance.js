const attendanceCsrf = document.querySelector('meta[name="csrf-token"]')?.content || '';

function attendanceToast(message, isError = false) {
  document.querySelector('.attendance-save-toast')?.remove();
  const toast = document.createElement('div');
  toast.className = `attendance-save-toast${isError ? ' error' : ''}`;
  toast.textContent = message;
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 2200);
}

document.querySelectorAll('.attendance-status-select').forEach((select) => {
  select.addEventListener('change', async () => {
    const previous = select.dataset.original || '';
    const status = select.value;
    if (!['P', 'A'].includes(status)) return;
    select.disabled = true;
    select.classList.add('saving');
    try {
      const response = await fetch('/api/attendance', {
        method: 'POST',
        credentials: 'same-origin',
        headers: {'Content-Type': 'application/json', 'X-CSRF-Token': attendanceCsrf},
        body: JSON.stringify({
          employee_key: select.dataset.employeeKey,
          attendance_date: select.dataset.attendanceDate,
          status_code: status,
        }),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.error || 'Attendance update failed');
      select.dataset.original = status;
      select.className = `attendance-status-select status-${status.toLowerCase()}`;
      attendanceToast(status === 'P' ? 'Marked Present' : 'Marked Absent');
    } catch (error) {
      select.value = previous;
      attendanceToast(error.message || 'Attendance update failed', true);
    } finally {
      select.disabled = false;
      select.classList.remove('saving');
    }
  });
});
