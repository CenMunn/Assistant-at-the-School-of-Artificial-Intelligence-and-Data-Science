() => {
  if (window.__collegeProfileCleanup) window.__collegeProfileCleanup();
  let wasOpen = false;
  let previousFocus = null;
  const getDialog = () => [...document.querySelectorAll('#profile-dialog, #conversation-confirmation')].find(visible);
  const visible = el => !!el && el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
  const controls = el => [...el.querySelectorAll('input,textarea,button,select,[tabindex="0"]')]
    .filter(x => !x.disabled && visible(x));
  const sync = () => {
    const dialog = getDialog();
    const open = visible(dialog);
    if (open === wasOpen) return;
    wasOpen = open;
    if (open) {
      previousFocus = document.activeElement;
      dialog.setAttribute('role', 'dialog');
      dialog.setAttribute('aria-modal', 'true');
      dialog.setAttribute('aria-label', dialog.id === 'profile-dialog' ? '个人资料' : '管理会话');
      document.querySelector('#workspace')?.setAttribute('inert', '');
      requestAnimationFrame(() => controls(dialog)[0]?.focus());
    } else {
      document.querySelector('#workspace')?.removeAttribute('inert');
      const target = previousFocus && document.contains(previousFocus) && previousFocus !== document.body
        ? previousFocus : document.querySelector('#conversation-new') || document.querySelector('#profile-open');
      target?.focus();
    }
  };
  const keydown = event => {
    const dialog = getDialog();
    if (!visible(dialog)) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      document.querySelector(dialog.id === 'profile-dialog' ? '#profile-skip' : '#conversation-cancel')?.click();
    }
    if (event.key === 'Tab') {
      const items = controls(dialog);
      if (!items.length) return;
      const index = items.indexOf(document.activeElement);
      if (event.shiftKey && index <= 0) { event.preventDefault(); items.at(-1).focus(); }
      else if (!event.shiftKey && (index === items.length - 1 || index === -1)) {
        event.preventDefault(); items[0].focus();
      }
    }
  };
  const toggle = (id, button, className) => {
    const panel = document.querySelector(id);
    if (!panel) return;
    const open = panel.classList.toggle(className);
    button.setAttribute('aria-expanded', String(open));
    button.setAttribute('aria-controls', panel.id);
    if (open) requestAnimationFrame(() => controls(panel)[0]?.focus());
  };
  const click = event => {
    const settings = document.querySelector('#chat-options');
    const rail = document.querySelector('#chat-settings');
    if (event.target.closest('#chat-options-toggle')) {
      toggle('#chat-options', event.target.closest('#chat-options-toggle'), 'settings-open');
    } else if (settings && !settings.contains(event.target)) {
      settings.classList.remove('settings-open');
      document.querySelector('#chat-options-toggle')?.setAttribute('aria-expanded', 'false');
    }
    if (event.target.closest('#history-toggle')) {
      toggle('#chat-settings', event.target.closest('#history-toggle'), 'history-open');
    } else if (rail && !rail.contains(event.target) && !event.target.closest('#conversation-confirmation')) {
      rail.classList.remove('history-open');
      document.querySelector('#history-toggle')?.setAttribute('aria-expanded', 'false');
    }
    document.querySelectorAll('.conversation-menu[open]').forEach(menu => {
      if (!menu.contains(event.target)) menu.open = false;
    });
  };
  const closePanels = event => {
    if (event.key !== 'Escape' || visible(getDialog())) return;
    for (const [id, cls, button] of [['#chat-options','settings-open','#chat-options-toggle'],
                                    ['#chat-settings','history-open','#history-toggle']]) {
      const panel = document.querySelector(id);
      if (panel?.classList.contains(cls)) {
        panel.classList.remove(cls);
        const control = document.querySelector(button);
        control?.setAttribute('aria-expanded', 'false'); control?.focus();
        event.preventDefault(); return;
      }
    }
  };
  const observer = new MutationObserver(sync);
  observer.observe(document.body, {childList:true, subtree:true, attributes:true, attributeFilter:['style','class','hidden']});
  document.addEventListener('keydown', keydown);
  document.addEventListener('keydown', closePanels);
  document.addEventListener('click', click);
  sync();
  window.__collegeProfileCleanup = () => {
    observer.disconnect(); document.removeEventListener('keydown', keydown);
    document.removeEventListener('keydown', closePanels); document.removeEventListener('click', click);
    document.querySelector('#workspace')?.removeAttribute('inert');
  };
}
