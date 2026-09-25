(() => {
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';

  const postJson = async (url, payload) => {
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
      body: JSON.stringify(payload),
    });
    const data = await response.json().catch(() => ({ ok: false, error: 'The server returned an unreadable response.' }));
    if (response.status === 401) window.location.href = '/login';
    return data;
  };

  document.querySelectorAll('[data-open-dialog]').forEach((button) => {
    button.addEventListener('click', () => document.getElementById(button.dataset.openDialog)?.showModal());
  });
  document.querySelectorAll('[data-close-dialog]').forEach((button) => {
    button.addEventListener('click', () => button.closest('dialog')?.close());
  });
  document.querySelectorAll('.modal').forEach((dialog) => {
    dialog.addEventListener('click', (event) => { if (event.target === dialog) dialog.close(); });
  });
  document.querySelectorAll('.edit-student').forEach((button) => {
    button.addEventListener('click', () => {
      const form = document.getElementById('edit-student-form');
      form.action = button.dataset.url;
      document.getElementById('edit-university-id').textContent = button.dataset.universityId || 'University ID';
      document.getElementById('edit-name').value = button.dataset.name;
      document.getElementById('edit-program').value = button.dataset.program;
      document.getElementById('edit-year').value = button.dataset.year;
      document.getElementById('edit-email').value = button.dataset.email;
      document.getElementById('edit-student-dialog').showModal();
    });
  });
  document.querySelectorAll('[data-toggle-password]').forEach((button) => {
    button.addEventListener('click', () => {
      const input = document.getElementById(button.dataset.togglePassword);
      input.type = input.type === 'password' ? 'text' : 'password';
      button.textContent = input.type === 'password' ? 'Show' : 'Hide';
    });
  });
  document.querySelectorAll('.flash-close').forEach((button) => button.addEventListener('click', () => button.closest('.flash')?.remove()));
  document.querySelectorAll('button[data-confirm]').forEach((button) => {
    button.addEventListener('click', (event) => {
      if (!window.confirm(button.dataset.confirm)) event.preventDefault();
    });
  });

  const copyDemo = document.querySelector('[data-copy-demo]');
  if (copyDemo) copyDemo.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText('admin\nSmartAttend@123');
      copyDemo.textContent = 'Copied';
      setTimeout(() => { copyDemo.textContent = 'Copy'; }, 1500);
    } catch { copyDemo.textContent = 'Select to copy'; }
  });

  const mobileMenu = document.getElementById('mobile-menu');
  mobileMenu?.addEventListener('click', () => document.body.classList.toggle('sidebar-open'));
  document.addEventListener('click', (event) => {
    if (document.body.classList.contains('sidebar-open') && !event.target.closest('#sidebar') && !event.target.closest('#mobile-menu')) {
      document.body.classList.remove('sidebar-open');
    }
  });

  const checkinForm = document.getElementById('id-checkin-form');
  if (checkinForm) {
    checkinForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      const input = document.getElementById('checkin-id');
      const feedback = document.getElementById('id-feedback');
      const button = checkinForm.querySelector('button[type="submit"]');
      if (!input.value.trim()) { input.focus(); return; }
      button.disabled = true;
      feedback.className = 'camera-feedback feedback-loading';
      feedback.textContent = 'Looking up university ID…';
      try {
        const result = await postJson('/api/attendance/manual', { university_id: input.value });
        feedback.className = `camera-feedback ${result.ok ? (result.inserted ? 'feedback-success' : 'feedback-info') : 'feedback-error'}`;
        feedback.innerHTML = result.ok
          ? `<strong>${result.inserted ? '✓ Attendance marked' : '↻ Already checked in'}</strong><span>${escapeHtml(result.student)} · ${escapeHtml(result.university_id)} · ${escapeHtml(result.time.slice(0, 5))}</span>`
          : escapeHtml(result.error || 'Could not mark attendance.');
        if (result.ok) input.value = '';
      } catch {
        feedback.className = 'camera-feedback feedback-error';
        feedback.textContent = 'Unable to reach the local attendance server.';
      } finally { button.disabled = false; }
    });
  }

  const escapeHtml = (value) => String(value).replace(/[&<>"']/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[character]));

  const cameraRoot = document.querySelector('#camera-widget, .camera-attendance-card[data-mode="attendance"]');
  if (cameraRoot) {
    const video = cameraRoot.querySelector('#camera-preview');
    const canvas = cameraRoot.querySelector('#camera-canvas');
    const placeholder = cameraRoot.querySelector('#camera-placeholder');
    const enableButton = cameraRoot.querySelector('#start-camera');
    const captureButton = cameraRoot.querySelector('#capture-face');
    const scanButton = cameraRoot.querySelector('#start-scan');
    const stopButton = cameraRoot.querySelector('#stop-camera');
    const statusText = cameraRoot.querySelector('#camera-status');
    const statusDot = cameraRoot.querySelector('#camera-status-dot');
    const feedback = cameraRoot.querySelector('#camera-feedback');
    let stream = null;
    let scanTimer = null;
    let scanBusy = false;
    let scanComplete = false;

    const setStatus = (text, active = false) => {
      if (statusText) statusText.textContent = text;
      if (statusDot) statusDot.classList.toggle('camera-on', active);
    };
    const showFeedback = (text, kind = 'info') => {
      if (!feedback) return;
      feedback.className = `camera-feedback ${kind ? `feedback-${kind}` : ''}`;
      feedback.textContent = text;
    };
    const waitForVideoFrame = () => new Promise((resolve, reject) => {
      if (!video) return reject(new Error('The camera preview is unavailable. Refresh the page and try again.'));
      if (video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA && video.videoWidth && video.videoHeight) return resolve();

      let timeout;
      const cleanup = () => {
        clearTimeout(timeout);
        video.removeEventListener('loadeddata', checkReady);
        video.removeEventListener('canplay', checkReady);
      };
      const checkReady = () => {
        if (video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA && video.videoWidth && video.videoHeight) {
          cleanup();
          resolve();
        }
      };
      video.addEventListener('loadeddata', checkReady);
      video.addEventListener('canplay', checkReady);
      timeout = setTimeout(() => {
        cleanup();
        reject(new Error('Camera started, but no picture arrived. Check camera permission and try again.'));
      }, 8000);
      checkReady();
    });
    const openCamera = async () => {
      if (!navigator.mediaDevices?.getUserMedia) {
        showFeedback('Camera access needs a secure page or localhost, and a browser that supports webcam access.', 'error');
        return false;
      }
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'user', width: { ideal: 1280 }, height: { ideal: 720 } }, audio: false });
        video.srcObject = stream;
        await video.play();
        await waitForVideoFrame();
        placeholder?.classList.add('hidden');
        enableButton?.classList.add('hidden');
        if (captureButton) captureButton.disabled = false;
        if (scanButton) scanButton.disabled = false;
        if (stopButton) stopButton.classList.remove('hidden');
        setStatus('Camera is on · face the lens', true);
        showFeedback('Center one face in the guide and use good lighting.', 'info');
        return true;
      } catch (error) {
        const reason = error.name === 'NotAllowedError'
          ? 'Camera permission was blocked. Allow camera access in your browser settings.'
          : error.message?.includes('camera picture') || error.message?.includes('camera preview') || error.message?.includes('no picture')
            ? error.message
            : 'Could not start the camera. Check that it is connected and not in use by another app.';
        if (stream) stream.getTracks().forEach((track) => track.stop());
        stream = null;
        if (video) video.srcObject = null;
        showFeedback(reason, 'error');
        setStatus('Camera could not start');
        return false;
      }
    };
    const stopCamera = () => {
      if (scanTimer) clearInterval(scanTimer);
      scanTimer = null;
      if (stream) stream.getTracks().forEach((track) => track.stop());
      stream = null;
      if (video) video.srcObject = null;
      placeholder?.classList.remove('hidden');
      enableButton?.classList.remove('hidden');
      if (captureButton) captureButton.disabled = true;
      if (scanButton) scanButton.disabled = true;
      stopButton?.classList.add('hidden');
      setStatus('Camera is off');
    };
    const snapshot = () => {
      if (!video?.videoWidth || !video.videoHeight || video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) {
        throw new Error('Wait for the camera picture, then try again.');
      }
      const scale = Math.min(1, 720 / video.videoWidth, 720 / video.videoHeight);
      canvas.width = Math.round(video.videoWidth * scale);
      canvas.height = Math.round(video.videoHeight * scale);
      const context = canvas.getContext('2d', { alpha: false });
      if (!context) throw new Error('The camera picture could not be prepared. Refresh the page and try again.');
      context.drawImage(video, 0, 0, canvas.width, canvas.height);
      return canvas.toDataURL('image/jpeg', 0.84);
    };

    enableButton?.addEventListener('click', openCamera);
    stopButton?.addEventListener('click', stopCamera);
    captureButton?.addEventListener('click', async () => {
      captureButton.disabled = true;
      showFeedback('Checking for a clear face…', 'loading');
      try {
        const image = snapshot();
        const result = await postJson(`/api/students/${cameraRoot.dataset.studentId}/faces`, { image });
        if (!result.ok) throw new Error(result.error || 'This photo could not be saved.');
        const count = document.getElementById('capture-count');
        const progress = document.getElementById('capture-progress');
        if (count) count.textContent = result.count;
        if (progress) progress.style.width = `${Math.min(100, result.count * 10)}%`;
        showFeedback(`✓ ${result.message} Keep going until you have at least five.`, 'success');
        setStatus(`${result.count} photos saved · ready for the next angle`, true);
      } catch (error) { showFeedback(error.message, 'error'); }
      finally { captureButton.disabled = !stream; }
    });

    scanButton?.addEventListener('click', async () => {
      scanComplete = false;
      scanButton.classList.add('hidden');
      stopButton?.classList.remove('hidden');
      setStatus('Scanning · hold still for a moment', true);
      showFeedback('The camera scans one frame at a time; a recognized student is saved once per day.', 'info');
      const scan = async () => {
        if (!stream || scanBusy || scanComplete) return;
        scanBusy = true;
        try {
          const result = await postJson('/api/attendance/recognize', { image: snapshot() });
          if (result.ok && result.recognized) {
            scanComplete = true;
            clearInterval(scanTimer); scanTimer = null;
            showFeedback(`${result.inserted ? '✓ Attendance marked' : '↻ Already checked in'} · ${result.student} (${result.university_id}) · ${Math.round(result.confidence * 100)}% match`, result.inserted ? 'success' : 'info');
            setStatus(`${result.student} · ${result.time.slice(0, 5)}`, true);
          } else if (result.error) {
            const unregistered = result.error === 'You are not registered here.';
            showFeedback(result.error, unregistered ? 'error' : 'info');
            setStatus(unregistered ? 'Unregistered face' : 'Scanning · adjust position or lighting', true);
          }
        } catch (error) { showFeedback(error.message || 'Could not scan this frame.', 'error'); }
        finally { scanBusy = false; }
      };
      await scan();
      if (stream && !scanTimer && !scanComplete) scanTimer = setInterval(scan, 1600);
    });
    stopButton?.addEventListener('click', () => { stopCamera(); scanComplete = false; scanButton?.classList.remove('hidden'); });
    window.addEventListener('pagehide', () => stream?.getTracks().forEach((track) => track.stop()));
  }

  const trainingCard = document.querySelector('.training-status-card[data-training-state="running"]');
  if (trainingCard) {
    const label = document.getElementById('training-state-label');
    const message = document.getElementById('training-message');
    const progress = document.getElementById('training-progress');
    const poll = async () => {
      try {
        const response = await fetch('/api/model/status', { headers: { 'Accept': 'application/json' } });
        const status = await response.json();
        if (message && status.message) message.textContent = status.message;
        if (label) {
          label.className = `training-state-label ${status.state === 'ready' ? 'state-ready' : status.state === 'error' ? 'state-error' : ''}`;
          label.innerHTML = `<i></i>${escapeHtml(status.state.toUpperCase())}`;
        }
        if (status.state !== 'running') { window.location.reload(); return; }
      } catch { /* Keep polling if a single local request fails. */ }
      setTimeout(poll, 1800);
    };
    setTimeout(poll, 1400);
  }
})();

