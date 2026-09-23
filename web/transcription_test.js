(() => {
    'use strict';
    const config = JSON.parse(document.getElementById('testConfig').textContent);
    const el = id => document.getElementById(id);
    let blob = null, objectUrl = '', recorder = null, stream = null, timer = null, pollTimer = null;
    let starting = false, recording = false, busy = false, disposed = false, jobId = '', resultText = '';
    const storageKey = 'sipTranscriptionTest';
    el('engine').value = config.backend;
    function message(id, text) { el(id).textContent = text; el(id).hidden = !text; }
    function status(text, working = false) { message('status', text); el('status').classList.toggle('working', working); }
    function controls() {
        el('record').disabled = busy || starting;
        el('record').textContent = recording ? '■ Остановить' : starting ? 'Доступ к микрофону…' : '● Записать голос';
        el('record').classList.toggle('recording', recording);
        el('audioFile').disabled = busy || recording || starting;
        el('dropzone').classList.toggle('disabled', el('audioFile').disabled);
        el('engine').disabled = busy || recording || starting;
        el('transcribe').disabled = !blob || busy || recording || starting;
        el('transcribe').textContent = busy ? 'Распознавание…' : 'Распознать';
    }
    function selectAudio(file, name) {
        if (!file || !file.size) { message('error', 'Запись пустая. Повторите запись.'); return; }
        if (file.size > config.maxBytes) { message('error', 'Файл превышает допустимый размер.'); return; }
        if (objectUrl) URL.revokeObjectURL(objectUrl);
        blob = file; objectUrl = URL.createObjectURL(blob); jobId = '';
        el('player').src = objectUrl; el('preview').hidden = false;
        el('filename').textContent = name; el('result').hidden = true;
        try { sessionStorage.removeItem(storageKey); } catch (_) {}
        message('error', ''); status(''); controls();
    }
    function stopTracks() { stream?.getTracks().forEach(track => track.stop()); stream = null; }
    function stopRecord() { if (recorder?.state === 'recording') recorder.stop(); }
    el('record').addEventListener('click', async () => {
        if (recording) { stopRecord(); return; }
        message('error', ''); starting = true; controls();
        try {
            if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) throw new Error('Запись недоступна в этом браузере. Выберите аудиофайл.');
            stream = await navigator.mediaDevices.getUserMedia({audio:true});
            if (disposed) { stopTracks(); return; }
            const mimeType = ['audio/webm;codecs=opus','audio/ogg;codecs=opus','audio/mp4'].find(type => MediaRecorder.isTypeSupported(type));
            recorder = new MediaRecorder(stream, mimeType ? {mimeType} : undefined);
            const chunks = []; let bytes = 0, failed = false;
            recorder.ondataavailable = event => { if (event.data.size) { chunks.push(event.data); bytes += event.data.size; if (bytes > config.maxBytes) stopRecord(); } };
            recorder.onerror = () => { failed = true; message('error', 'Не удалось записать звук. Повторите запись.'); stopRecord(); stopTracks(); clearInterval(timer); recording = false; controls(); };
            recorder.onstop = () => {
                clearInterval(timer); stopTracks(); recording = false;
                if (!disposed && !failed) selectAudio(new Blob(chunks, {type:recorder.mimeType}), 'Запись с микрофона');
                controls();
            };
            el('player').pause(); recorder.start(1000); recording = true;
            const began = Date.now(); el('timer').textContent = '0:00';
            timer = setInterval(() => {
                const sec = Math.floor((Date.now()-began)/1000);
                el('timer').textContent = `${Math.floor(sec/60)}:${String(sec%60).padStart(2,'0')}`;
                if (sec >= 300) stopRecord();
            }, 500);
        } catch (error) {
            stopTracks(); message('error', error.name === 'NotAllowedError' ? 'Разрешите доступ к микрофону или выберите файл.' : error.message || 'Микрофон недоступен.');
        } finally { starting = false; controls(); }
    });
    el('audioFile').addEventListener('change', event => {
        const file = event.target.files[0]; if (file) selectAudio(file, file.name); event.target.value = '';
    });
    for (const type of ['dragenter','dragover']) el('dropzone').addEventListener(type, event => {
        event.preventDefault(); if (!el('audioFile').disabled) el('dropzone').classList.add('dragging');
    });
    el('dropzone').addEventListener('dragleave', () => el('dropzone').classList.remove('dragging'));
    el('dropzone').addEventListener('drop', event => {
        event.preventDefault(); el('dropzone').classList.remove('dragging');
        if (el('audioFile').disabled) return;
        if (event.dataTransfer.files.length !== 1) { message('error','Выберите один аудиофайл.'); return; }
        const file = event.dataTransfer.files[0]; selectAudio(file, file.name);
    });
    // Dropping outside the target must not navigate away from the recording.
    window.addEventListener('dragover', event => event.preventDefault());
    window.addEventListener('drop', event => event.preventDefault());
    async function request(action, options = {}, id = '') {
        const url = new URL(location.href); url.searchParams.set('action', action);
        if (id) url.searchParams.set('id', id);
        const response = await fetch(url, {credentials:'same-origin', cache:'no-store', ...options, signal:AbortSignal.timeout(90000)});
        let data; try { data = await response.json(); } catch (_) { throw new Error('Сервер недоступен. Повторите проверку.'); }
        if (!response.ok || !data.ok) { const error = new Error(data.message || 'Не удалось выполнить запрос.'); error.status = response.status; throw error; }
        return data;
    }
    function showJob(job) {
        if (job.backend) el('engine').value = job.backend;
        if (job.status === 'queued' || job.status === 'processing') {
            busy = true; status(job.status === 'queued' ? 'Ожидание распознавания…' : 'Распознавание…', true); controls(); return false;
        }
        busy = false; status(''); controls();
        if (job.status === 'failed') { message('error', job.message || 'Не удалось распознать запись.'); jobId = ''; try { sessionStorage.removeItem(storageKey); } catch (_) {} return true; }
        resultText = job.text || ''; el('resultText').textContent = resultText || (job.speech_detected ? 'Речь обнаружена, но текст не распознан.' : 'Речь не обнаружена.');
        el('resultMeta').textContent = `${job.backend === 'gigaam' ? 'GigaAM' : 'faster-whisper'} · Аудио ${job.duration ?? 0} с · Обработка ${job.elapsed ?? 0} с`;
        el('copy').disabled = !resultText; el('result').hidden = false; jobId = ''; return true;
    }
    async function poll() {
        clearTimeout(pollTimer);
        if (disposed || !jobId) return;
        try {
            const data = await request('status', {}, jobId); message('error','');
            if (showJob(data.job)) return;
        } catch (error) {
            if (error.status === 400 || error.status === 401 || error.status === 403) {
                busy = false; status(''); message('error', error.message); controls(); return;
            }
            status('Ожидание связи с сервером…', true);
        }
        pollTimer = setTimeout(poll, 3000);
    }
    el('transcribe').addEventListener('click', async () => {
        if (!blob || busy || recording || starting) return;
        clearTimeout(pollTimer); busy = true; message('error',''); el('result').hidden = true; controls(); status('Загрузка аудио…', true);
        // A retry of an uncertain upload keeps its ID; changing audio/engine clears it.
        if (!jobId) jobId = Array.from(crypto.getRandomValues(new Uint8Array(16)), b=>b.toString(16).padStart(2,'0')).join('');
        const body = new FormData(); body.set('id', jobId); body.set('backend', el('engine').value);
        body.set('csrf_token', config.csrf); body.set('audio', blob, 'sample.audio');
        try {
            const data = await request('enqueue', {method:'POST', body});
            try { sessionStorage.setItem(storageKey, jobId); } catch (_) {}
            if (!showJob(data.job)) pollTimer = setTimeout(poll, 1000);
        } catch (error) {
            busy = false; status(''); message('error', error.message || 'Не удалось загрузить аудио. Повторите попытку.'); controls();
        }
    });
    el('engine').addEventListener('change', () => { jobId = ''; });
    el('copy').addEventListener('click', async () => {
        try { await navigator.clipboard.writeText(resultText); el('copy').textContent = 'Скопировано'; setTimeout(()=>el('copy').textContent='Скопировать', 2000); }
        catch (_) { message('error','Не удалось скопировать. Выделите текст результата.'); }
    });
    window.addEventListener('pagehide', () => { disposed = true; clearInterval(timer); clearTimeout(pollTimer); stopRecord(); stopTracks(); if (objectUrl) URL.revokeObjectURL(objectUrl); });
    window.addEventListener('pageshow', event => { if (event.persisted) location.reload(); });
    try { jobId = sessionStorage.getItem(storageKey) || ''; } catch (_) {}
    if (/^[a-f0-9]{32}$/.test(jobId)) { busy = true; status('Загрузка результата…', true); poll(); }
    controls();
})();
