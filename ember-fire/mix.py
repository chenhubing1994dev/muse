from __future__ import annotations
import json, math, wave
from pathlib import Path
import numpy as np
from scipy.signal import butter, sosfilt

ROOT = Path(__file__).resolve().parent
VOICE = ROOT / "voice"
OUT = ROOT / "out"
OUT.mkdir(exist_ok=True)
SR = 48000
DUR = 45.0
N = int(SR * DUR)
TARGETS = [2.0, 7.0, 13.0, 19.0, 26.0, 34.5, 41.0]

def read_wav(path: Path):
    with wave.open(str(path), "rb") as w:
        sr, ch, sw, frames = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
        raw = w.readframes(frames)
    if sw == 2:
        x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif sw == 4:
        x = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"Unsupported PCM sample width {sw}: {path}")
    if ch > 1: x = x.reshape(-1, ch).mean(axis=1)
    return x, sr

def resample(x, src, dst):
    if src == dst: return x
    m = max(1, int(round(len(x) * dst / src)))
    return np.interp(np.linspace(0, len(x)-1, m), np.arange(len(x)), x).astype(np.float32)

def add_stereo(bus, x, at, pan=0.0, gain=1.0):
    i = max(0, int(at * SR)); j = min(N, i + len(x))
    if j <= i: return
    y = x[:j-i] * gain
    # equal-power pan
    l = math.sqrt((1-pan)*0.5)*math.sqrt(2)
    r = math.sqrt((1+pan)*0.5)*math.sqrt(2)
    bus[i:j, 0] += y*l
    bus[i:j, 1] += y*r

def tone(freq, seconds, amp=0.1, decay=1.8, harmonics=(1.0, .26, .08)):
    n = max(1, int(seconds*SR)); t = np.arange(n, dtype=np.float32)/SR
    env = np.exp(-t*decay) * np.minimum(1, t*80)
    y = np.zeros(n, dtype=np.float32)
    for k,h in enumerate(harmonics, 1):
        y += h*np.sin(2*np.pi*freq*k*t + .13*k)
    return (y*env*amp).astype(np.float32)

def noise_burst(seconds, amp=.04, cutoff=2500, seed=8):
    rng = np.random.default_rng(seed); n=int(seconds*SR)
    x=rng.normal(0,1,n).astype(np.float32)
    sos=butter(2, cutoff/(SR/2), btype='low', output='sos')
    x=sosfilt(sos,x).astype(np.float32)
    x/=max(1e-6,float(np.max(np.abs(x))))
    env=np.maximum(0.0,np.sin(np.linspace(0,math.pi,n,dtype=np.float32)))**1.4
    return x*env*amp

def stamp(bus, at, freq=58, amp=.22, seconds=.9, pan=0):
    x=tone(freq,seconds,amp,decay=4.0,harmonics=(1,.18,.04))
    add_stereo(bus,x,at,pan)
    add_stereo(bus,noise_burst(.28,amp*.18,900,seed=int(at*1000)+4),at,pan)

def main():
    lines=json.loads((VOICE/"lines.json").read_text(encoding="utf-8"))
    bus=np.zeros((N,2),dtype=np.float32)
    voice_bus=np.zeros((N,2),dtype=np.float32)
    timings=[]; prev_audio=0.0; prev_sub=0.0
    for i,L in enumerate(lines):
        wav=VOICE/(L["id"]+".wav")
        if not wav.exists(): raise FileNotFoundError(wav)
        x,sr=read_wav(wav); x=resample(x,sr,SR)
        # Keep each Cantonese line intact; move only when the previous subtitle needs more reading time.
        start=max(TARGETS[i],prev_audio+.30,prev_sub+.18)
        end=start+len(x)/SR
        read_hold=max(2.6,len(L["text"])/4.5+1.5)
        sub_end=max(end+.62,start+read_hold)
        if sub_end>DUR-.15: raise RuntimeError(f"Voice/subtitle timing exceeds 45s at {L['id']}: {sub_end:.2f}s")
        timings.append({"id":L["id"],"text":L["text"],"start":round(start,3),"end":round(end,3),"subtitle_end":round(sub_end,3),"duration":round(len(x)/SR,3)})
        # Gentle de-essing is left to the source TTS; level by RMS, then cap peaks.
        rms=float(np.sqrt(np.mean(x*x))+1e-8)
        x=x*(.23/rms)
        peak=float(np.max(np.abs(x))+1e-8)
        if peak>.90: x=x*(.90/peak)
        fade=min(int(.035*SR),len(x)//4)
        if fade>0:
            x[:fade]*=np.linspace(0,1,fade,dtype=np.float32)
            x[-fade:]*=np.linspace(1,0,fade,dtype=np.float32)
        add_stereo(voice_bus,x,start,pan=0,gain=1.0)
        prev_audio=end; prev_sub=sub_end

    # Original score: sparse pentatonic plucks, low pulse, breathy drones and a restrained electronic bed.
    t=np.arange(N,dtype=np.float32)/SR
    breath=noise_burst(DUR,.014,360,seed=42)
    bus[:,0]+=breath*.7; bus[:,1]+=breath*.82
    drone=(np.sin(2*np.pi*55*t)*.018 + np.sin(2*np.pi*82.4*t+.7)*.010 + np.sin(2*np.pi*110*t+1.2)*.006)
    bus[:,0]+=drone; bus[:,1]+=drone*.96
    # Low-frequency cinematic swell; fades in and out around the emotional arc.
    swell=(.5+.5*np.sin(2*np.pi*t/18-.9)) * np.clip(t/5,0,1) * np.clip((DUR-t)/4,0,1)
    pad=(np.sin(2*np.pi*146.8*t)*.012 + np.sin(2*np.pi*220*t)*.008 + np.sin(2*np.pi*293.7*t)*.004)
    bus[:,0]+=pad*swell; bus[:,1]+=pad*swell

    notes=[
      (0.7,110,.13,.9,-.2),(3.1,164.8,.12,1.1,.2),(6.7,146.8,.13,.8,-.3),
      (9.4,196,.10,.9,.25),(13.3,220,.12,1.2,-.2),(16.2,246.9,.10,.9,.2),
      (19.4,164.8,.14,.8,-.25),(20.5,220,.14,.7,.25),(22.2,293.7,.12,.8,-.1),
      (24.1,329.6,.13,.9,.2),(26.3,392,.14,.7,-.25),(28.2,220,.18,1.4,.1),
      (30.0,293.7,.14,1.2,-.2),(32.1,329.6,.14,1.2,.2),(34.6,392,.12,1.0,-.1),
      (37.0,293.7,.14,1.1,.2),(39.9,220,.16,1.5,0),(42.0,164.8,.10,1.7,0)
    ]
    for at,f,a,d,p in notes: add_stereo(bus,tone(f,d,a,decay=2.4),at,p)
    # The two silences are intentional: music and ambience fall away before the knowledge bridge and seal.
    silence=np.ones(N,dtype=np.float32)
    for center,width in [(19.0,.42),(39.3,.62)]:
        lo=max(0,int((center-width)*SR)); hi=min(N,int((center+width)*SR))
        if hi>lo:
            # Smooth symmetric dip to near silence; narration remains untouched.
            u=np.linspace(-1,1,hi-lo,dtype=np.float32)
            silence[lo:hi]=np.minimum(silence[lo:hi],.06+.94*np.abs(u)**1.7)
    # Duck music/ambience only under the voice, not the dialogue itself.
    duck=np.ones(N,dtype=np.float32)
    for q in timings:
        a=max(0,int((q["start"]-.12)*SR)); b=min(N,int((q["end"]+.18)*SR))
        if b>a: duck[a:b]=np.minimum(duck[a:b],.34)
    bus*= (duck*silence)[:,None]
    bus+=voice_bus

    # Foley: ember crackle, ink bloom, page turn, glassy connections, city impact, seal.
    for at,f,a,d in [(2.1,48,.24,.7),(6.6,62,.19,.65),(13.0,74,.13,.55),(20.1,92,.16,.5),(28.0,52,.27,1.0),(39.8,44,.30,1.0)]:
        stamp(bus,at,f,a,d)
    for at,f in [(20.4,660),(21.3,880),(22.5,990),(24.2,1320),(29.1,784),(31.2,1046),(35.8,1175),(40.1,523)]:
        add_stereo(bus,tone(f,.75,.055,decay=5.5,harmonics=(1,.3,.1)),at,pan=.15)
    for at in [6.3,12.8,18.8,25.5,33.0,39.2]:
        add_stereo(bus,noise_burst(.65,.035,1900,seed=int(at*91)),at,pan=-.1)

    # Soft saturation and peak guard; final LUFS/true peak mastering is performed by FFmpeg.
    bus=np.tanh(bus*1.25)*.78
    peak=float(np.max(np.abs(bus)))
    if peak>.94: bus*=.94/peak
    pcm=np.int16(np.clip(bus,-1,1)*32767)
    out=OUT/"mix.wav"
    with wave.open(str(out),"wb") as w:
        w.setnchannels(2);w.setsampwidth(2);w.setframerate(SR);w.writeframes(pcm.tobytes())

    (OUT/"voice_timing.json").write_text(json.dumps(timings,ensure_ascii=False,indent=2),encoding="utf-8")
    def stamp_srt(v):
        ms=round(v*1000); h=ms//3600000; ms%=3600000; m=ms//60000; ms%=60000; s=ms//1000; ms%=1000
        return f"{h:02}:{m:02}:{s:02},{ms:03}"
    srt=[]
    for i,q in enumerate(timings,1):
        srt += [str(i),f"{stamp_srt(q['start'])} --> {stamp_srt(q['subtitle_end'])}",q["text"],""]
    (ROOT/"火种不灭.srt").write_text("\n".join(srt),encoding="utf-8")
    print(json.dumps({"mix":str(out),"duration":DUR,"voice_timing":timings,"peak_before_master":peak},ensure_ascii=False,indent=2))

if __name__=="__main__": main()
