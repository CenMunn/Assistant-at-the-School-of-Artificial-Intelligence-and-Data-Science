let requested = false, draftNode = null, draft = '', scheduled = false;
let follow = true, lastToken = null;
const jump = button('↓', '回到最新消息', () => {
  follow = true;
  const scroll = document.querySelector('#chat .bubble-wrap');
  if (scroll) scroll.scrollTop = scroll.scrollHeight;
  jump.hidden = true;
});
jump.className = 'chat-jump'; jump.hidden = true;
document.querySelector('#chat-workspace')?.append(jump);

function syncScroll(state) {
  const scroll = document.querySelector('#chat .bubble-wrap');
  if (!scroll) return;
  if (!scroll.__assistantFollow) {
    scroll.__assistantFollow = true;
    scroll.addEventListener('scroll', () => {
      follow = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 80;
      jump.hidden = follow;
    }, {passive:true});
    scroll.addEventListener('wheel', event => { if (event.deltaY < 0) follow = false; }, {passive:true});
  }
  if (state.token !== lastToken && state.busy) follow = true;
  lastToken = state.token;
  if (follow && !state.editing) scroll.scrollTop = scroll.scrollHeight;
  jump.hidden = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 80;
}
function submit(kind, node, text) {
  if (requested || props.value.busy) return;
  requested = true;
  props.value = {...props.value, action: {kind, node_id: node, view_token: props.value.token, text}};
  trigger('input');
  render();
}
function button(label, title, onClick) {
  const item = document.createElement('button');
  item.type = 'button'; item.textContent = label; item.title = title;
  item.setAttribute('aria-label', title); item.addEventListener('click', onClick);
  return item;
}
function render() {
  scheduled = false;
  const state = props.value;
  if (!state || state.action) return;
  const rows = document.querySelectorAll('#chat .message-row.user-row');
  if (state.editing && draftNode !== state.editing) { draftNode = state.editing; draft = state.draft || ''; }
  if (!state.editing && draftNode) { draftNode = null; draft = ''; }
  rows.forEach((row, index) => {
    const node = state.nodes[index]; if (!node) return;
    const host = row.querySelector('.flex-wrap'); if (!host) return;
    const signature = JSON.stringify([node, state.token, state.editing, state.busy, state.blocked, requested]);
    let toolbar = host.querySelector('.chat-user-tools');
    if (toolbar?.dataset.signature === signature) return;
    toolbar?.remove();
    host.querySelector('.inline-chat-editor')?.remove();
    const bubble = host.querySelector('.message.user');
    if (bubble) bubble.hidden = state.editing === node.id;
    toolbar = document.createElement('div'); toolbar.className = 'chat-user-tools';
    toolbar.dataset.signature = signature; toolbar.dataset.node = node.id;
    const disabled = requested || state.busy || state.blocked;
    if (state.editing === node.id) {
      const editor = document.createElement('div'); editor.className = 'inline-chat-editor';
      const field = document.createElement('textarea'); field.value = draft;
      field.setAttribute('aria-label', '编辑已发送的消息'); field.rows = Math.min(8, Math.max(3, draft.split('\n').length));
      field.addEventListener('input', () => draft = field.value);
      window.AssistantKeys.bindSendKeys(field, () => { if (!disabled) submit('send', node.id, field.value); });
      field.disabled = disabled;
      editor.append(field); host.append(editor);
      toolbar.append(button('取消', '取消编辑', () => submit('cancel', node.id)),
                     button('发送', '发送修改后的消息', () => submit('send', node.id, field.value)));
      toolbar.lastChild.classList.add('edit-send');
      requestAnimationFrame(() => { if (!disabled) { field.focus(); field.setSelectionRange(field.value.length, field.value.length); } });
    } else {
      toolbar.append(button('编辑', '编辑这条消息', () => submit('begin', node.id)));
      if (node.versions.length > 1) {
        const previous = button('‹', '上一个消息版本', () => submit('switch', node.versions[node.index - 1]));
        const next = button('›', '下一个消息版本', () => submit('switch', node.versions[node.index + 1]));
        previous.disabled = node.index === 0; next.disabled = node.index === node.versions.length - 1;
        const label = document.createElement('span'); label.textContent = `${node.index + 1} / ${node.versions.length}`;
        label.setAttribute('aria-label', `消息版本 ${node.index + 1}，共 ${node.versions.length} 个`);
        toolbar.append(previous, label, next);
      }
    }
    toolbar.querySelectorAll('button').forEach(item => { if (disabled || (state.editing && state.editing !== node.id)) item.disabled = true; });
    host.append(toolbar);
  });
  document.querySelectorAll('#chat .message-row.bot-row').forEach((row, index) => {
    const notice = state.nodes[index]?.notice || '';
    const host = row.querySelector('.flex-wrap');
    if (!host) return;
    let marker = host.querySelector('.chat-answer-status');
    if (!notice) { marker?.remove(); return; }
    if (!marker) { marker = document.createElement('div'); marker.className = 'chat-answer-status'; marker.setAttribute('role', 'note'); host.append(marker); }
    if (marker.textContent !== notice) marker.textContent = notice;
  });
  const question = document.querySelector('#question textarea');
  if (question) window.AssistantKeys.bindSendKeys(question, () => {
    const send = document.querySelector('#send');
    if (!send?.disabled && !props.value.busy && !props.value.editing) send.click();
  });
  syncScroll(state);
}
function schedule() { if (!scheduled) { scheduled = true; requestAnimationFrame(render); } }
const observer = new MutationObserver(schedule);
observer.observe(document.querySelector('#chat-workspace') || document.body, {childList: true, subtree: true});
watch('value', () => {
  if (props.value.action) return;
  requested = false;
  const wasEditing = draftNode;
  schedule();
  if (wasEditing && !props.value.editing) requestAnimationFrame(() => {
    document.querySelector(`.chat-user-tools[data-node="${wasEditing}"] button`)?.focus();
    if (!document.querySelector(`.chat-user-tools[data-node="${wasEditing}"]`)) document.querySelector('#question textarea')?.focus();
  });
});
schedule();
