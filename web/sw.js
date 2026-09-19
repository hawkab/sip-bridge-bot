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

const CACHE_NAME = 'sip-pwa-v4';
const ASSETS = [
  './manifest.webmanifest',
  '/icons/192-any.png',
  './icons/512-any.png',
  './icons/192-mask.png',
  './icons/512-mask.png'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(ASSETS)).catch(() => Promise.resolve())
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(
      keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))
    ))
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') {
    return;
  }
  const url = new URL(event.request.url);
  if (event.request.mode === 'navigate' || url.searchParams.has('action')
      || url.pathname.endsWith('/transcription_settings.php')) {
    return;
  }

  event.respondWith(
    caches.match(event.request).then((cached) => cached || fetch(event.request))
  );
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
