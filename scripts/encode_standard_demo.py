"""Encode a continuous browser screencast at its original timing, without visual edits."""
import json
import math
import os
import subprocess
from pathlib import Path

from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'.cache/standard-video-capture'
DEST=ROOT/'docs/demo/refund-shark-standard-demo.mp4'
FPS=30


def main():
    record=json.loads((SOURCE/'recording.json').read_text())
    frames=record['frames']
    if not frames or record['duration']>181:
        raise SystemExit('Recording missing or longer than the requested three minutes')
    count=math.ceil(record['duration']*FPS)
    DEST.parent.mkdir(parents=True,exist_ok=True)
    encoder=subprocess.Popen([str(ROOT/'.cache/video-encoder'),'encode',str(DEST),'1920','1080',str(FPS)],stdin=subprocess.PIPE,
        env={**os.environ,'SHARK_VIDEO_BITRATE':'3500000'})
    index=0
    last=-1
    raw=None
    try:
        for n in range(count):
            while index+1<len(frames) and frames[index+1]['t']<=n/FPS:
                index+=1
            if index!=last:
                with Image.open(SOURCE/frames[index]['file']) as frame:
                    assert abs(frame.width/frame.height-16/9)<.005
                    if frame.size!=(1920,1080):frame=frame.resize((1920,1080),Image.Resampling.LANCZOS)
                    raw=frame.convert('RGBA').tobytes('raw','BGRA')
                last=index
            encoder.stdin.write(raw)
            if n%(15*FPS)==0:print(f'Encoded {n/FPS:.0f}/{count/FPS:.1f}s',flush=True)
        encoder.stdin.close()
        if encoder.wait()!=0:raise SystemExit('MP4 encoder failed')
    finally:
        if encoder.poll() is None:encoder.terminate()
    info={'file':DEST.name,'duration_seconds':count/FPS,'resolution':[1920,1080],'capture_resolution':[1280,720],'fps':FPS,'audio':'Silent for voice-over',
        'source':'Continuous browser screencast. Original timing, normal pointer and scrolling; no title cards, camera crops, or artificial zooms.',
        'browser_frames':len(frames),'chapters':record['chapters']}
    (DEST.parent/'standard-video-info.json').write_text(json.dumps(info,indent=2)+'\n')
    print(f'Saved {DEST} ({DEST.stat().st_size/1_000_000:.1f} MB)')


if __name__=='__main__':main()
