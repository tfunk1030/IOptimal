import pystray
from PIL import Image, ImageDraw
import webbrowser
import threading
from watcher.watch import start_watcher
from watcher.uploader import IBTUploader

def create_image(color):
    image = Image.new('RGB', (64, 64), color)
    d = ImageDraw.Draw(image)
    d.rectangle((16, 16, 48, 48), fill='white')
    return image

class WatcherApp:
    def __init__(self, uploader: IBTUploader, car: str):
        self.uploader = uploader
        self.car = car
        self.icon = None
        self.watcher_thread = None

    def start(self):
        self.watcher_thread = threading.Thread(target=start_watcher, args=(self.uploader, self.car), daemon=True)
        self.watcher_thread.start()

        menu = pystray.Menu(
            pystray.MenuItem('Open Dashboard', self.open_dashboard),
            pystray.MenuItem('Quit', self.stop)
        )
        self.icon = pystray.Icon("iOptimal", create_image('green'), "iOptimal Watcher", menu)
        self.icon.run()

    def open_dashboard(self, icon, item):
        webbrowser.open(f"{self.uploader.server_url}/dashboard")

    def stop(self, icon, item):
        self.icon.stop()

if __name__ == "__main__":
    uploader = IBTUploader("http://localhost:8000", "dummy_token", "driver_123")
    app = WatcherApp(uploader, "bmw")
    app.start()
