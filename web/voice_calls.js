/* Voice recorder and durable scheduled-call composer. No recording starts without a click. */
window.SipVoiceCalls = (() => {
    const MAX_BYTES = 2 * 1024 * 1024;
    let api, state = fresh(), recorder, stream, timer, started = 0, generation = 0;
    function fresh() { return {number:'', when:'', mode:'now', blob:null, url:'', jobs:[], connected:false, csrf:'', busy:false, recording:false, starting:false, error:'', message:''}; }
    function escape(value) { return api.escape(String(value ?? '')); }
    function date(value) { return new Intl.DateTimeFormat('ru-RU', {timeZone:'Europe/Moscow', dateStyle:'short', timeStyle:'medium'}).format(new Date(value)) + ' МСК'; }
    function update() { if (api.active()) api.refresh(); }
    function setAudio(blob) {
        if (state.url) URL.revokeObjectURL(state.url);
        state.blob = blob; state.url = blob ? URL.createObjectURL(blob) : '';
    }
    function sync() {
        const form = document.getElementById('voiceCallForm');
        if (!form) return;
        state.number = form.elements.number.value.trim();
        state.mode = form.elements.mode.value;
        state.when = form.elements.when.value;
    }
    function history() {
        return state.jobs.map(job => `<article class="border rounded p-3 mb-2">
            <div class="d-flex justify-content-between flex-wrap gap-2"><strong>${escape(job.number)}</strong><span>${escape(date(job.scheduled_at))}</span></div>
            <div>${escape(job.message)}</div><small class="text-muted">${escape(job.source)} · ${escape(job.id)}</small>
            ${job.status === 'queued' ? `<div class="mt-2"><button type="button" class="btn btn-sm btn-outline-danger" data-cancel-voice="${escape(job.id)}">Отменить вызов</button></div>` : ''}
        </article>`).join('') || '<p class="text-muted">Вызовов пока нет.</p>';
    }
    function render() {
        return `<div class="card shadow-sm mb-3"><div class="card-body">
            <h2 class="h5">Голосовое сообщение звонком</h2>
            <p class="text-muted">GSM-шлюз позвонит по номеру и воспроизведёт запись после ответа. До 120 секунд и 2 МБ.</p>
            <div id="voiceConnection" class="small mb-3">${state.connected ? 'Сервер вызовов подключён' : 'Сервер вызовов не отвечает. Задание сохранится; опоздание более 10 минут отменит вызов.'}</div>
            ${state.error ? `<div class="alert alert-danger" role="alert">${escape(state.error)}</div>` : ''}
            ${state.message ? `<div class="alert alert-success" role="status">${escape(state.message)}</div>` : ''}
            <form id="voiceCallForm"><fieldset ${state.busy ? 'disabled' : ''}>
            <label class="form-label" for="voiceNumber">Номер получателя</label>
            <input id="voiceNumber" name="number" type="tel" class="form-control mb-3" placeholder="+79991234567" pattern="\\+[1-9][0-9]{6,14}" required value="${escape(state.number)}">
            <label class="form-label" for="voiceMode">Когда позвонить</label>
            <select id="voiceMode" name="mode" class="form-select mb-3"><option value="now" ${state.mode === 'now' ? 'selected' : ''}>Сейчас</option><option value="scheduled" ${state.mode === 'scheduled' ? 'selected' : ''}>В указанное время</option></select>
            <div ${state.mode === 'scheduled' ? '' : 'hidden'}><label class="form-label" for="voiceWhen">Дата и время — Москва (UTC+3)</label>
            <input id="voiceWhen" name="when" type="datetime-local" class="form-control mb-3" value="${escape(state.when)}" ${state.mode === 'scheduled' ? 'required' : ''}></div>
            <div class="d-flex gap-2 align-items-center flex-wrap mb-3">
                <button id="voiceRecord" type="button" class="btn ${state.recording ? 'btn-danger' : 'btn-outline-primary'}" ${state.starting ? 'disabled' : ''}>${state.recording ? 'Остановить запись' : state.starting ? 'Доступ к микрофону…' : 'Надиктовать сообщение'}</button>
                <span id="voiceElapsed" role="status">${state.recording ? 'Идёт запись…' : ''}</span>
            </div>
            <label class="form-label" for="voiceFile">Или выберите аудиофайл</label>
            <input id="voiceFile" type="file" accept="audio/*,.webm,.ogg,.m4a,.wav,.mp3" class="form-control mb-3" ${state.recording || state.starting ? 'disabled' : ''}>
            ${state.url ? `<label class="form-label d-block">Прослушать перед отправкой</label><audio controls src="${escape(state.url)}" class="w-100 mb-3"></audio>` : ''}
            <button class="btn btn-primary" type="submit" ${!state.blob || state.recording || state.starting || !state.csrf ? 'disabled' : ''}>${state.busy ? 'Сохранение…' : state.mode === 'scheduled' ? 'Запланировать вызов' : 'Позвонить и воспроизвести'}</button>
            </fieldset></form>
        </div></div><h2 class="h5">Вызовы и расписание</h2><div id="voiceHistory">${history()}</div>`;
    }
    async function load() {
        const response = await api.get('get_voice_outbox');
        state.jobs = response.jobs; state.connected = response.connected; state.csrf = response.csrf_token;
    }
    async function poll() {
        await load();
        const element = document.getElementById('voiceHistory');
        if (element) element.innerHTML = history();
        const connection = document.getElementById('voiceConnection');
        if (connection) connection.textContent = state.connected ? 'Сервер вызовов подключён' : 'Сервер вызовов не отвечает. Задание сохранится; опоздание более 10 минут отменит вызов.';
    }
    function releaseMicrophone() {
        clearInterval(timer); timer = null;
        if (stream) stream.getTracks().forEach(track => track.stop());
        stream = null;
    }
    async function record() {
        sync();
        if (state.recording) { recorder.stop(); return; }
        if (state.starting || state.busy) return;
        state.error = ''; state.message = ''; state.starting = true;
        const current = ++generation;
        update();
        try {
            if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) throw new Error('Запись с микрофона недоступна в этом браузере. Выберите аудиофайл.');
            const acquired = await navigator.mediaDevices.getUserMedia({audio:true});
            if (generation !== current) { acquired.getTracks().forEach(track => track.stop()); return; }
            stream = acquired;
            const mimeType = ['audio/webm;codecs=opus','audio/ogg;codecs=opus','audio/mp4'].find(type => MediaRecorder.isTypeSupported(type));
            recorder = new MediaRecorder(stream, mimeType ? {mimeType} : undefined);
            let chunks = [], size = 0, failed = false;
            recorder.ondataavailable = event => {
                if (event.data.size) { chunks.push(event.data); size += event.data.size; }
                if (size > MAX_BYTES && recorder.state === 'recording') { failed = true; state.error = 'Запись превысила 2 МБ. Запишите более короткое сообщение.'; recorder.stop(); }
            };
            recorder.onerror = () => { failed = true; state.error = 'Ошибка микрофона. Повторите запись или загрузите аудиофайл.'; };
            recorder.onstop = () => {
                releaseMicrophone(); state.recording = false; state.starting = false;
                if (current === generation && !failed) setAudio(new Blob(chunks, {type:recorder.mimeType}));
                update();
            };
            setAudio(null); state.starting = false; state.recording = true;
            recorder.start(1000); started = Date.now();
            timer = setInterval(() => {
                const seconds = Math.floor((Date.now()-started)/1000);
                const label = document.getElementById('voiceElapsed');
                if (label) label.textContent = `${seconds} / 120 сек.`;
                if (seconds >= 120 && recorder.state === 'recording') recorder.stop();
            }, 250);
            update();
        } catch (error) {
            releaseMicrophone(); state.starting = false; state.recording = false;
            state.error = error.name === 'NotAllowedError' ? 'Разрешите доступ к микрофону в браузере или выберите аудиофайл.' : error.message;
            update();
        }
    }
    async function submit(event) {
        if (event.target.id !== 'voiceCallForm') return;
        event.preventDefault(); sync();
        if (state.busy || state.recording || state.starting || !state.blob) return;
        state.error = ''; state.message = '';
        try {
            if (state.blob.size > MAX_BYTES || state.blob.size < 32) throw new Error('Нужна аудиозапись до 2 МБ.');
            const when = state.mode === 'scheduled' ? state.when+':00+03:00' : '';
            if (when && (!state.when || !Number.isFinite(Date.parse(when)) || Date.parse(when) <= Date.now())) throw new Error('Выберите будущее время (Москва).');
            state.busy = true; update();
            const hash = [...new Uint8Array(await crypto.subtle.digest('SHA-256', await state.blob.arrayBuffer()))].map(x => x.toString(16).padStart(2,'0')).join('');
            const fingerprint = `${state.number}|${when}|${hash}`;
            let pending;
            try { pending = JSON.parse(sessionStorage.getItem('sipVoicePending') || 'null'); } catch (_) {}
            if (!pending || pending.fingerprint !== fingerprint) pending = {fingerprint, key:crypto.randomUUID()};
            try { sessionStorage.setItem('sipVoicePending', JSON.stringify(pending)); } catch (_) {}
            const body = new FormData();
            body.set('number', state.number); body.set('scheduled_at', when); body.set('request_key', pending.key);
            body.set('csrf_token', state.csrf); body.set('audio', state.blob, 'voice.audio');
            const response = await api.upload(body);
            try { sessionStorage.removeItem('sipVoicePending'); } catch (_) {}
            state.message = `Вызов сохранён: ${response.job.number}, ${date(response.job.scheduled_at)}.`;
            setAudio(null);
            await load();
        } catch (error) { state.error = error.message || 'Не удалось сохранить вызов. Проверьте список и повторите отправку той же записи.'; }
        finally { state.busy = false; update(); }
    }
    function leave(clear = false) {
        sync();
        if (state.starting) { generation++; state.starting = false; }
        if (state.recording && recorder?.state === 'recording') recorder.stop();
        releaseMicrophone();
        if (clear) { generation++; setAudio(null); state = fresh(); try { sessionStorage.removeItem('sipVoicePending'); } catch (_) {} }
    }
    function bind(root, dependencies) {
        api = dependencies;
        root.addEventListener('submit', submit);
        root.addEventListener('input', event => { if (event.target.closest('#voiceCallForm')) sync(); });
        root.addEventListener('change', event => {
            if (event.target.id === 'voiceMode') { sync(); update(); }
            if (event.target.id === 'voiceFile') {
                sync(); const file = event.target.files[0];
                if (!file) return;
                if (file.size > MAX_BYTES) { state.error = 'Файл превышает 2 МБ.'; setAudio(null); }
                else { state.error = ''; state.message = ''; setAudio(file); }
                update();
            }
        });
        root.addEventListener('click', async event => {
            if (event.target.closest('#voiceRecord')) { await record(); return; }
            const cancel = event.target.closest('[data-cancel-voice]');
            if (!cancel) return;
            cancel.disabled = true;
            try { await api.post('cancel_voice_call', {id:cancel.dataset.cancelVoice, csrf_token:state.csrf}); await poll(); }
            catch (error) { state.error = error.message; update(); }
        });
        window.addEventListener('pagehide', () => leave());
    }
    return {bind, render, load, poll, leave};
})();
