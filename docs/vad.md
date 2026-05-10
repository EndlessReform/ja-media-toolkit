## VAD desiderata

We want to create a library in `mlx-core`


```python
from mlx_audio.vad import load

model = load("mlx-community/silero-vad")
timestamps = model.get_speech_timestamps("audio.wav", return_seconds=True)
print(timestamps)
```gg
