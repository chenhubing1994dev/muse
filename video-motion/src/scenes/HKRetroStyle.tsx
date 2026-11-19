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

export type HkPalette = 'cha-chaan-teng' | 'neon-alley' | 'sunset-street';

export type HKRetroStyleProps = {
  palette: HkPalette;
  neonText: string;
  subtitle: string;
  vhsIntensity: number;
  width: number;
  height: number;
  fps: number;
  durationInFrames: number;
};

type PaletteDef = {bg1: string; bg2: string; warm: string; neon1: string; neon2: string};

const PALETTES: Record<HkPalette, PaletteDef> = {
  'cha-chaan-teng': {bg1: '#4a2410', bg2: '#0d0705', warm: '#ffb347', neon1: '#ff2d95', neon2: '#ffd93d'},
  'neon-alley': {bg1: '#210d33', bg2: '#05030f', warm: '#ff5e8a', neon1: '#00e5ff', neon2: '#ff2d95'},
  'sunset-street': {bg1: '#5a2410', bg2: '#140a12', warm: '#ff8c42', neon1: '#ff3864', neon2: '#ffd166'},
};

export const defaultHKRetroStyleProps: HKRetroStyleProps = {
  palette: 'cha-chaan-teng',
  neonText: '霓虹映象',
  subtitle: '今夜，让灵感照亮街角。',
  vhsIntensity: 0.6,
  width: 1080,
  height: 1920,
  fps: 30,
  durationInFrames: 180,
};

export const calculateHKRetroStyleMetadata: CalculateMetadataFunction<HKRetroStyleProps> = ({props}) => {
  const width = Math.max(64, Math.min(3840, Math.round(props.width || 1080)));
  const height = Math.max(64, Math.min(3840, Math.round(props.height || 1920)));
  const fps = Math.max(24, Math.min(60, Math.round(props.fps || 30)));
  const durationInFrames = Math.max(30, Math.min(1200, Math.round(props.durationInFrames || 180)));
  return {width, height, fps, durationInFrames};
};

// Deterministic pseudo-random driven by an integer frame bucket.
const hash01 = (index: number, salt = 0): number => {
  const v = Math.sin(index * 127.1 + salt * 311.7) * 43758.5453123;
  return v - Math.floor(v);
};

export const HKRetroStyle: React.FC<HKRetroStyleProps> = (props) => {
  const frame = useCurrentFrame();
  const {width, height, fps, durationInFrames} = useVideoConfig();
  const p = PALETTES[props.palette] ?? PALETTES['cha-chaan-teng'];
  const vhs = Math.max(0, Math.min(1, props.vhsIntensity ?? 0.6));
  const chars = Array.from(props.neonText || '霓虹映象');
  const glowX = 50 + Math.sin(frame / 38) * 6;
  const subEnter = spring({frame, fps, config: {damping: 18, stiffness: 90, mass: 0.8}});
  const subOpacity = interpolate(frame, [0, 12, durationInFrames - 18, durationInFrames], [0, 1, 1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.inOut(Easing.quad),
  });
  const noiseCount = Math.round(14 * vhs) + 4;
  const signs = ['茶', '餐', '廳', '夜', '市'];
  return (
    <AbsoluteFill style={{overflow: 'hidden', fontFamily: '"Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", sans-serif'}}>
      <AbsoluteFill style={{background: `radial-gradient(ellipse at ${glowX}% 32%, ${p.bg1} 0%, ${p.bg2} 72%)`}} />
      <AbsoluteFill style={{opacity: 0.32}}>
        {signs.map((s, i) => {
          const left = 6 + i * 19;
          const top = 8 + (i % 3) * 16;
          const flick = 0.55 + 0.45 * Math.abs(Math.sin((frame + i * 11) / 9));
          const signColor = i % 2 ? p.neon2 : p.neon1;
          return (
            <div
              key={'sign-' + i}
              style={{
                position: 'absolute',
                left: `${left}%`,
                top: `${top}%`,
                width: width * 0.12,
                height: height * 0.11,
                border: `3px solid ${signColor}`,
                background: `${signColor}14`,
                color: signColor,
                fontSize: width * 0.075,
                fontWeight: 900,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                opacity: flick,
                boxShadow: `0 0 ${width * 0.03}px ${signColor}66`,
              }}
            >
              {s}
            </div>
          );
        })}
      </AbsoluteFill>
      <div style={{position: 'absolute', top: height * 0.16, right: width * 0.1, writingMode: 'vertical-rl', textOrientation: 'upright', display: 'flex', gap: width * 0.02}}>
        {chars.map((ch, i) => {
          const flicker = interpolate(Math.sin((frame + i * 7) / 5.5), [-1, 1], [0.45, 1]);
          const col = i % 2 === 0 ? p.neon1 : p.neon2;
          return (
            <span
              key={'n-' + i}
              style={{
                fontSize: width * 0.1,
                fontWeight: 900,
                color: col,
                opacity: flicker,
                textShadow: `${3 * vhs}px 0 ${p.neon1}, ${-3 * vhs}px 0 ${p.neon2}, 0 0 ${width * 0.035}px ${col}`,
              }}
            >
              {ch}
            </span>
          );
        })}
      </div>
      <AbsoluteFill style={{background: `linear-gradient(0deg, ${p.warm}22, transparent 45%)`, mixBlendMode: 'screen'}} />
      <AbsoluteFill
        style={{
          pointerEvents: 'none',
          background: 'repeating-linear-gradient(0deg, rgba(0,0,0,0.22) 0px, rgba(0,0,0,0.22) 2px, transparent 2px, transparent 4px)',
          opacity: 0.35 + vhs * 0.4,
        }}
      />
      <AbsoluteFill
        style={{
          pointerEvents: 'none',
          background: `linear-gradient(90deg, ${p.neon1}18, transparent 12%, transparent 88%, ${p.neon2}18)`,
          mixBlendMode: 'screen',
          opacity: vhs,
        }}
      />
      <AbsoluteFill style={{pointerEvents: 'none', opacity: vhs}}>
        {Array.from({length: noiseCount}, (_, i) => {
          const seedBucket = Math.floor(frame / 2);
          const y = hash01(i, seedBucket) * height;
          const w = width * (0.18 + hash01(i, seedBucket + 1) * 0.5);
          const h = 2 + hash01(i, seedBucket + 2) * 6;
          const a = 0.08 + hash01(i, seedBucket + 3) * 0.22;
          return (
            <div
              key={'nz-' + i}
              style={{
                position: 'absolute',
                left: hash01(i, seedBucket + 4) * (width - w),
                top: y,
                width: w,
                height: h,
                background: i % 2 ? '#ffffff' : '#ff77bb',
                opacity: a,
              }}
            />
          );
        })}
      </AbsoluteFill>
      <div
        style={{
          position: 'absolute',
          left: width * 0.07,
          right: width * 0.07,
          bottom: height * 0.09,
          textAlign: 'center',
          opacity: subOpacity,
          transform: `translateY(${(1 - subEnter) * 28}px)`,
        }}
      >
        <span
          style={{
            color: '#ffe600',
            fontSize: width * 0.062,
            fontWeight: 900,
            lineHeight: 1.4,
            WebkitTextStroke: `${Math.max(2, width * 0.003)}px #ffffff`,
            textShadow: `0 0 ${width * 0.02}px ${p.neon1}, 2px 3px 0 #00000088`,
          }}
        >
          {props.subtitle}
        </span>
      </div>
      <div style={{position: 'absolute', bottom: height * 0.045, left: 0, right: 0, textAlign: 'center', color: p.warm, fontSize: width * 0.022, letterSpacing: 5, opacity: 0.7}}>
        MUSE · 港風 Retrowave
      </div>
    </AbsoluteFill>
  );
};
