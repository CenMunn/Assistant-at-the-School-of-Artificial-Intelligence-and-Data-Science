"""结果先由浏览器应用，再释放资料切换锁；不把业务执行结束当成传输结束。"""
from contextlib import contextmanager
from inspect import isgeneratorfunction
from uuid import uuid4
import gradio as gr
from app_session import operation, _ensure_idle


def acknowledge_delivery(app, token):
    with app.lock:
        app.pending_deliveries.discard(token)


def delivery_event(event, fn, inputs, outputs, *, profile_event=False, **options):
    # 独立的隐藏控件在响应应用后由 .then 回传。不能改成服务端 State：
    # State 会在网络传输前更新，不能证明浏览器已经收到本轮结果。
    receipt = gr.Textbox(visible=False, value='')
    @contextmanager
    def scope(app, revision):
        try:
            if profile_event:
                with app.lock:
                    _ensure_idle(app)
                    yield
            else:
                with operation(app, revision):
                    yield
        except ValueError as error:
            raise gr.Error(str(error)) from None

    def pack(app, value, token):
        with app.lock:
            app.pending_deliveries.add(token)
        values = list(value) if len(outputs) > 1 else [value]
        return [*values, token]

    def regular(*args):
        app, revision = args[-2:]
        with scope(app, revision):
            return pack(app, fn(*args), uuid4().hex)

    def streaming(*args):
        app, revision = args[-2:]
        token = uuid4().hex
        with scope(app, revision):
            for value in fn(*args):
                yield pack(app, value, token)

    callback = streaming if isgeneratorfunction(fn) else regular
    callback.__name__ = fn.__name__
    callback.raw_callback = fn
    dependency = event(callback, inputs, [*outputs, receipt], **options)
    acknowledged = dependency.then(acknowledge_delivery, [inputs[-2], receipt], [], queue=False,
                    api_name=False, show_progress='hidden', trigger_mode='multiple')
    return acknowledged
