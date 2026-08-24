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
      const rowInputs = [...document.querySelectorAll(`.leave-balance-input[data-employee-key="${select.dataset.employeeKey}"]`)];
      rowInputs.forEach((item) => {
        item.value = item.dataset.balanceKind === 'pl' ? result.pl_balance : result.sl_balance;
        item.dataset.original = item.value;
      });
      attendanceToast(`Attendance marked ${status || 'blank'}`);
    } catch (error) {
      select.value = previous;
      attendanceToast(error.message || 'Attendance update failed', true);
    } finally {
      select.disabled = false;
      select.classList.remove('saving');
    }
  });
});

document.querySelectorAll('.leave-balance-input').forEach((input) => {
  input.dataset.original = input.value;
  input.addEventListener('change', async () => {
    const employeeKey = input.dataset.employeeKey;
    const rowInputs = [...document.querySelectorAll(`.leave-balance-input[data-employee-key="${employeeKey}"]`)];
    const pl = rowInputs.find((item) => item.dataset.balanceKind === 'pl');
    const sl = rowInputs.find((item) => item.dataset.balanceKind === 'sl');
    rowInputs.forEach((item) => { item.disabled = true; item.classList.add('saving'); });
    try {
      const response = await fetch('/api/attendance/balance', {
        method: 'POST', credentials: 'same-origin',
        headers: {'Content-Type': 'application/json', 'X-CSRF-Token': attendanceCsrf},
        body: JSON.stringify({employee_key: employeeKey, pl_balance: pl.value, sl_balance: sl.value}),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.error || 'Leave balance update failed');
      pl.value = result.pl_balance; sl.value = result.sl_balance;
      rowInputs.forEach((item) => { item.dataset.original = item.value; });
      attendanceToast('PL and SL balances updated');
    } catch (error) {
      rowInputs.forEach((item) => { item.value = item.dataset.original; });
      attendanceToast(error.message || 'Leave balance update failed', true);
    } finally {
      rowInputs.forEach((item) => { item.disabled = false; item.classList.remove('saving'); });
    }
  });
});
