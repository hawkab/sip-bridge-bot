function toAbsoluteUrl(value) {
  try {
    return new URL(value || './index.php', self.location.origin).href;
  } catch (error) {
    return new URL('./index.php', self.location.origin).href;
  }
}

function normalizeComparableUrl(value) {
  try {
    const url = new URL(value, self.location.origin);
    url.hash = '';
    return url.toString();
  } catch (error) {
    return '';
  }
}

function isSameOrigin(url) {
  try {
    return new URL(url, self.location.origin).origin === self.location.origin;
  } catch (error) {
    return false;
  }
}

async function focusWindowClient(client) {
  if (!client || typeof client.focus !== 'function') {
    return client || null;
  }

  try {
    return await client.focus();
  } catch (error) {
    return client;
  }
}

async function navigateWindowClient(client, url) {
  if (!client || typeof client.navigate !== 'function') {
    return client || null;
  }

  try {
    return await client.navigate(url);
  } catch (error) {
    return client;
  }
}

function postOpenTargetMessage(client, url, notificationData) {
  if (!client || typeof client.postMessage !== 'function') {
    return;
  }

  try {
    client.postMessage({
      type: 'OPEN_NOTIFICATION_TARGET',
      url,
      notificationData: notificationData || {}
    });
  } catch (error) {
  }
}

const CACHE_NAME = 'sip-pwa-v5';
const OFFLINE_URL = new URL('./offline.html', self.location.href).href;
const ASSETS = ['./icons/192-any.png', './icons/512-any.png', './icons/192-mask.png', './icons/512-mask.png'];

self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE_NAME);
    // Install only when the standalone fallback is available. Optional icons
    // must not prevent offline navigation from working.
    await cache.add(new Request(OFFLINE_URL, { cache: 'reload' }));
    await Promise.allSettled(ASSETS.map(asset => cache.add(asset)));
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter(key => key.startsWith('sip-pwa-') && key !== CACHE_NAME).map(key => caches.delete(key)));
    await self.clients.claim();
  })());
});

async function navigateWithOfflineFallback(request) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 8000);
  try {
    const response = await fetch(request, { signal: controller.signal });
    if (response.status >= 500) throw new Error('Server unavailable');
    return response;
  } catch (error) {
    const cache = await caches.open(CACHE_NAME);
    const offline = await cache.match(OFFLINE_URL);
    if (offline) return offline;
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;
  const url = new URL(event.request.url);
  const appDirectory = new URL('./', self.location.href).pathname;
  if (url.origin !== self.location.origin || url.searchParams.has('action')) return;
  if (event.request.mode === 'navigate' && (url.pathname === appDirectory || url.pathname === appDirectory + 'index.php')) {
    event.respondWith(navigateWithOfflineFallback(event.request));
    return;
  }
  // Never cache private pages, API responses or recordings.
  const assets = ASSETS.map(asset => new URL(asset, self.location.href).href);
  if (assets.includes(url.href)) {
    event.respondWith(caches.open(CACHE_NAME).then(cache => cache.match(event.request)).then(cached => cached || fetch(event.request)));
  }
});

self.addEventListener('push', (event) => {
  let payload = {
    title: 'Новое событие',
    body: 'Поступили новые данные.',
    url: './index.php',
    tag: 'sip-updates',
    type: 'generic'
  };

  if (event.data) {
    let rawText = '';

    try {
      payload = { ...payload, ...event.data.json() };
    } catch (error) {
      try {
        payload.body = event.data.text();
      } catch (e) {
      }

      try {
        rawText = event.data.text();
      } catch (e) {
      }
    }

    if (rawText) {
      payload.rawText = rawText;
    }
  }

  event.waitUntil(
    self.registration.showNotification(payload.title, {
      body: payload.body,
      tag: payload.tag || 'sip-updates',
      data: {
        url: toAbsoluteUrl(payload.url || './index.php'),
        type: payload.type || 'generic'
      },
      icon: './icons/512-any.png',
      badge: './icons/192-any.png',
      renotify: true
    })
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();

  event.waitUntil((async () => {
    const notificationData = event.notification.data || {};
    const absoluteTargetUrl = toAbsoluteUrl(notificationData.url || './index.php');
    const comparableTargetUrl = normalizeComparableUrl(absoluteTargetUrl);
    const clientList = await self.clients.matchAll({
      type: 'window',
      includeUncontrolled: true
    });

    const exactClient = clientList.find((client) => (
      isSameOrigin(client.url) &&
      normalizeComparableUrl(client.url) === comparableTargetUrl
    ));

    if (exactClient) {
      const focusedClient = await focusWindowClient(exactClient);
      postOpenTargetMessage(focusedClient || exactClient, absoluteTargetUrl, notificationData);
      return;
    }

    if (self.clients.openWindow) {
      try {
        const openedClient = await self.clients.openWindow(absoluteTargetUrl);

        if (openedClient) {
          const focusedClient = await focusWindowClient(openedClient);
          postOpenTargetMessage(focusedClient || openedClient, absoluteTargetUrl, notificationData);
          return;
        }
      } catch (error) {
      }
    }

    const sameOriginClient = clientList.find((client) => isSameOrigin(client.url));

    if (sameOriginClient) {
      let activeClient = await focusWindowClient(sameOriginClient);
      activeClient = await navigateWindowClient(activeClient || sameOriginClient, absoluteTargetUrl);
      activeClient = await focusWindowClient(activeClient || sameOriginClient);
      postOpenTargetMessage(activeClient || sameOriginClient, absoluteTargetUrl, notificationData);
    }
  })());
});
