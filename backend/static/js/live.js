// Live updates: WebSocket nudges (Django Channels) + polling fallback.
// App.live(refresh, {interval}) calls refresh() immediately, on every WebSocket event, and every
// `interval` ms (slower while the socket is healthy). The socket carries no medical data -
// refresh() always fetches it from a permission-checked endpoint.
(function () {
  App.live = function (refresh, opts = {}) {
    const fast = opts.interval || 4000, slow = opts.slowInterval || 15000;
    let ws = null, wsOk = false, retry = 0, timer = null, running = false;
    const status = opts.onStatus || (() => {});
    async function run(evt) {
      if (running) return;
      running = true;
      try { await refresh(evt); } catch (e) { /* next poll retries */ } finally { running = false; }
    }
    function schedule() { clearTimeout(timer); timer = setTimeout(() => { run(); schedule(); }, wsOk ? slow : fast); }
    function connect() {
      try {
        ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/notify/`);
      } catch (e) { status('polling'); return; }
      ws.onopen = () => { wsOk = true; retry = 0; status('live'); schedule(); };
      ws.onmessage = e => { let d = {}; try { d = JSON.parse(e.data); } catch (x) { /* ignore */ } run(d); };
      ws.onclose = () => {
        wsOk = false; status('polling'); schedule();
        retry = Math.min(retry + 1, 6);
        setTimeout(connect, 1000 * Math.pow(2, retry)); // 2 s .. 64 s
      };
    }
    run(); schedule(); connect();
    return {refresh: run};
  };
})();
