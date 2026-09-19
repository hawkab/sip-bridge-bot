/* Ask once on entry, with a one-time gesture fallback for browsers that require it. */
window.SipNotifications = {
    start(onGranted) {
        if (!('Notification' in window)) return;
        let inFlight = false;
        let subscribed = false;
        const removeFallback = () => {
            window.removeEventListener('pointerup', onGesture);
            window.removeEventListener('keydown', onGesture);
        };
        const onGesture = () => {
            removeFallback();
            request(true);
        };
        async function request(fromGesture = false) {
            if (inFlight || subscribed || Notification.permission === 'denied') return;
            inFlight = true;
            try {
                const permission = Notification.permission === 'granted'
                    ? 'granted' : await Notification.requestPermission();
                if (permission === 'granted') {
                    removeFallback();
                    await onGranted();
                    subscribed = true;
                } else if (permission === 'default' && !fromGesture) {
                    window.addEventListener('pointerup', onGesture, {once:true});
                    window.addEventListener('keydown', onGesture, {once:true});
                }
            } catch (error) {
                console.error('Не удалось включить уведомления:', error);
                if (!fromGesture && Notification.permission === 'default') {
                    window.addEventListener('pointerup', onGesture, {once:true});
                    window.addEventListener('keydown', onGesture, {once:true});
                }
            } finally {
                inFlight = false;
            }
        }
        request();
    },
};
