import React from 'react';
import {
  AbsoluteFill,
  Easing,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
  type CalculateMetadataFunction,
} from 'remotion';

export type CosmicVistaProps = {
  title: string;
  kicker: string;
  density: number;
  speed: number;
  width: number;
  height: number;
  fps: number;
  durationInFrames: number;
};

export const defaultCosmicVistaProps: CosmicVistaProps = {
  title: '从未知到理解',
  kicker: 'COSMIC VISTA / MUSE',
  density: 140,
  speed: 1,
  width: 1080,
  height: 1920,
  fps: 30,
  durationInFrames: 180,
};

export const calculateCosmicVistaMetadata: CalculateMetadataFunction<CosmicVistaProps> = ({props}) => {
  const width = Math.max(64, Math.min(3840, Math.round(props.width || 1080)));
  const height = Math.max(64, Math.min(3840, Math.round(props.height || 1920)));
  const fps = Math.max(24, Math.min(60, Math.round(props.fps || 30)));
  const durationInFrames = Math.max(30, Math.min(1200, Math.round(props.durationInFrames || 180)));
  return {width, height, fps, durationInFrames};
};

// Deterministic hash-based pseudo-random from an integer index.
const hash01 = (index: number, salt = 0): number => {
  const v = Math.sin(index * 127.1 + salt * 311.7) * 43758.5453123;
  return v - Math.floor(v);
};

export const CosmicVista: React.FC<CosmicVistaProps> = (props) => {
  const frame = useCurrentFrame();
  const {width, height, fps, durationInFrames} = useVideoConfig();
  const density = Math.max(10, Math.min(600, Math.round(props.density || 140)));
  const speed = Math.max(0, Math.min(6, props.speed ?? 1));
  const nebulaX = 50 + Math.sin(frame / 57) * 8;
  const nebulaY = 42 + Math.cos(frame / 63) * 6;
  const holoEnter = spring({frame: Math.max(0, frame - Math.round(durationInFrames * 0.3)), fps, config: {damping: 18, stiffness: 95, mass: 0.8}});
  const tunnelP = interpolate(frame, [0, 45], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.out(Easing.cubic),
  });
  const tunnelOpacity = interpolate(frame, [0, 10, 40, 52], [0, 1, 1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const rays = Array.from({length: 28});
  const cx = width / 2;
  const cy = height * 0.46;
  return (
    <AbsoluteFill style={{overflow: 'hidden', fontFamily: '"Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", sans-serif'}}>
      <AbsoluteFill style={{background: `radial-gradient(ellipse at ${nebulaX}% ${nebulaY}%, #3a1d6e 0%, #102247 38%, #050a18 78%, #02040c 100%)`}} />
      <AbsoluteFill
        style={{
          background: 'radial-gradient(ellipse at 78% 74%, #ff2d9522 0%, transparent 42%), radial-gradient(ellipse at 20% 26%, #00e5ff22 0%, transparent 40%)',
          mixBlendMode: 'screen',
        }}
      />
      <AbsoluteFill>
        {Array.from({length: density}, (_, i) => {
          const sx = hash01(i, 1) * width;
          const drift = frame * speed * (0.5 + hash01(i, 3) * 2.2) * 2.4;
          const sy = (hash01(i, 2) * height + drift) % height;
          const size = 1.4 + hash01(i, 4) * 3.6;
          const twinkle = 0.3 + 0.7 * Math.abs(Math.sin((frame + i * 17) / 16));
          const col = i % 7 === 0 ? '#ffe600' : i % 5 === 0 ? '#ff8ad1' : '#dff4ff';
          return (
            <div
              key={'st-' + i}
              style={{
                position: 'absolute',
                left: sx,
                top: sy,
                width: size,
                height: size,
                borderRadius: '50%',
                backgroundColor: col,
                opacity: twinkle,
                boxShadow: `0 0 ${size * 3}px ${col}`,
              }}
            />
          );
        })}
      </AbsoluteFill>
      <AbsoluteFill style={{opacity: tunnelOpacity, mixBlendMode: 'screen'}}>
        <svg width={width} height={height} style={{position: 'absolute', inset: 0}}>
          {rays.map((_, i) => {
            const ang = (i / rays.length) * Math.PI * 2;
            const inner = width * 0.03 + tunnelP * width * 0.28;
            const outer = inner + width * (0.12 + tunnelP * 0.75);
            const x1 = cx + Math.cos(ang) * inner;
            const y1 = cy + Math.sin(ang) * inner;
            const x2 = cx + Math.cos(ang) * outer;
            const y2 = cy + Math.sin(ang) * outer;
            return (
              <line
                key={'ry-' + i}
                x1={x1}
                y1={y1}
                x2={x2}
                y2={y2}
                stroke={i % 3 === 0 ? '#00e5ff' : '#ffffff'}
                strokeWidth={1 + hash01(i, 9) * 2}
                strokeOpacity={0.15 + 0.6 * (1 - tunnelP)}
              />
            );
          })}
        </svg>
      </AbsoluteFill>
      <div
        style={{
          position: 'absolute',
          left: width * 0.1,
          right: width * 0.1,
          top: height * 0.34,
          padding: width * 0.07,
          border: '2px solid #00e5ff',
          borderRadius: 6,
          background: 'linear-gradient(135deg, rgba(0,229,255,0.10), rgba(10,20,45,0.55))',
          boxShadow: '0 0 40px #00e5ff33, inset 0 0 30px #00e5ff18',
          opacity: holoEnter,
          transform: `translateY(${(1 - holoEnter) * 36}px) scale(${interpolate(holoEnter, [0, 1], [0.94, 1])})`,
          textAlign: 'center',
        }}
      >
        <div
          style={{
            position: 'absolute',
            inset: 0,
            background: 'repeating-linear-gradient(0deg, rgba(0,229,255,0.06) 0px, rgba(0,229,255,0.06) 1px, transparent 1px, transparent 6px)',
            pointerEvents: 'none',
          }}
        />
        <div style={{color: '#7cf6ff', fontSize: width * 0.028, letterSpacing: 5, marginBottom: height * 0.02}}>{props.kicker}</div>
        <div style={{color: '#eaffff', fontSize: width * 0.082, fontWeight: 900, lineHeight: 1.25, textShadow: '0 0 24px #00e5ff88'}}>{props.title}</div>
        <div style={{margin: `${height * 0.03}px auto 0`, width: '55%', height: 3, background: 'linear-gradient(90deg,transparent,#00e5ff,transparent)'}} />
      </div>
      <div style={{position: 'absolute', bottom: height * 0.06, left: 0, right: 0, textAlign: 'center', color: '#6f8fbf', fontSize: width * 0.02, letterSpacing: 4}}>
        MUSE · DETERMINISTIC COSMOS
      </div>
    </AbsoluteFill>
  );
};
