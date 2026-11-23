"""ByteTrack association with an instance-owned ID allocator."""
from ultralytics.trackers.byte_tracker import BYTETracker


class IsolatedBYTETracker(BYTETracker):
    def __init__(self, *args, **kwargs):
        self._local_track_id = 0
        super().__init__(*args, **kwargs)

    @staticmethod
    def reset_id():
        # BYTETracker.__init__/reset must not reset another camera's counter.
        pass

    def _next_track_id(self):
        self._local_track_id += 1
        return self._local_track_id

    def init_track(self, results, img=None):
        tracks = super().init_track(results, img)
        for track in tracks:
            track.next_id = self._next_track_id
        return tracks
