let pendingOpen = null;
element.addEventListener('click', event => {
  const summary = event.target.closest('summary');
  if (summary) {
    element.querySelectorAll('details[open]').forEach(item => {
      if (item !== summary.parentElement) item.open = false;
    });
    return;
  }
  const button = event.target.closest('button[data-action]');
  if (!button) return;
  const action = {kind: button.dataset.action, id: button.dataset.id};
  element.querySelectorAll('details[open]').forEach(item => item.open = false);
  if (action.kind === 'open') pendingOpen = action.id;
  props.value = {...props.value, action};
  trigger('input');
});
element.addEventListener('keydown', event => {
  if (event.key !== 'Escape') return;
  const menu = event.target.closest('details[open]');
  if (menu) { event.preventDefault(); menu.open = false; menu.querySelector('summary').focus(); }
});
watch('value', () => {
  if (props.value.action) return;
  if (pendingOpen && props.value.current === pendingOpen) {
    document.querySelector('#chat-settings')?.classList.remove('history-open');
    document.querySelector('#history-toggle')?.setAttribute('aria-expanded', 'false');
    requestAnimationFrame(() => document.querySelector('#question textarea')?.focus());
  }
  pendingOpen = null;
});
