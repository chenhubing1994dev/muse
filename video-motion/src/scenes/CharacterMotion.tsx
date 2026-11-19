import React from 'react';
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
  type CalculateMetadataFunction,
} from 'remotion';

export type CharacterStyle = 'cyber' | 'retro' | 'pixel';
export type CharacterExpression = 'surprising' | 'thinking' | 'happy' | 'serious';

export type CharacterMotionProps = {
  style: CharacterStyle;
  expression: CharacterExpression;
  label: string;
  speakingFrames: number;
  mouthAmplitude: number;
  width: number;
  height: number;
  fps: number;
  durationInFrames: number;
};

type StyleDef = {
  bg: string;
  skin: string;
  hair: string;
  accent: string;
  outline: string;
  font: string;
  radius: number;
};

const STYLES: Record<CharacterStyle, StyleDef> = {
  cyber: {
    bg: 'radial-gradient(circle at 50% 40%, #12324a 0%, #08101f 55%, #03060e 100%)',
    skin: '#d8f6ff',
    hair: '#1b6f9c',
    accent: '#00e5ff',
    outline: '#7cf6ff',
    font: '"Microsoft YaHei", "PingFang SC", sans-serif',
    radius: 90,
  },
  retro: {
    bg: 'radial-gradient(circle at 50% 40%, #5a3218 0%, #1a0e08 60%, #080402 100%)',
    skin: '#ffd3a6',
    hair: '#3a1d10',
    accent: '#ffb347',
    outline: '#ffd166',
    font: '"Microsoft YaHei", "PingFang SC", sans-serif',
    radius: 90,
  },
  pixel: {
    bg: '#0b0b1a',
    skin: '#ffd9a0',
    hair: '#7a00ff',
    accent: '#ffe600',
    outline: '#ffffff',
    font: '"JetBrains Mono", "Cascadia Mono", Consolas, monospace',
    radius: 0,
  },
};

export const defaultCharacterMotionProps: CharacterMotionProps = {
  style: 'cyber',
  expression: 'happy',
  label: 'MUSE · 讲解员',
  speakingFrames: 120,
  mouthAmplitude: 1,
  width: 1080,
  height: 1920,
  fps: 30,
  durationInFrames: 180,
};

export const calculateCharacterMotionMetadata: CalculateMetadataFunction<CharacterMotionProps> = ({props}) => {
  const width = Math.max(64, Math.min(3840, Math.round(props.width || 1080)));
  const height = Math.max(64, Math.min(3840, Math.round(props.height || 1920)));
  const fps = Math.max(24, Math.min(60, Math.round(props.fps || 30)));
  const durationInFrames = Math.max(30, Math.min(1200, Math.round(props.durationInFrames || 180)));
  return {width, height, fps, durationInFrames};
};

export const CharacterMotion: React.FC<CharacterMotionProps> = (props) => {
  const frame = useCurrentFrame();
  const {width, height, fps, durationInFrames} = useVideoConfig();
  const s = STYLES[props.style] ?? STYLES.cyber;
  const exp = props.expression ?? 'happy';
  const amp = Math.max(0, Math.min(2, props.mouthAmplitude ?? 1));
  const speakingFrames = Math.max(0, Math.round(props.speakingFrames));
  const isSpeaking = frame < speakingFrames;
  const mouthOpen = isSpeaking
    ? interpolate(Math.sin((frame / fps) * 14), [-1, 1], [0.15, 0.95], {
        extrapolateLeft: 'clamp',
        extrapolateRight: 'clamp',
      }) * amp
    : 0;
  const bob = Math.sin(frame / 11) * (width * 0.004);
  const enter = spring({frame, fps, config: {damping: 14, stiffness: 100, mass: 0.8}});
  const out = 1;
  const headW = width * 0.46;
  const headH = headW * 1.1;
  const eyeTop = headH * 0.3;
  const eyeGap = headW * 0.2;
  const mouthW = headW * (exp === 'surprising' ? 0.16 : 0.24);
  const mouthBaseH = exp === 'happy' ? headW * 0.04 : headW * 0.025;
  const mouthH = mouthBaseH + mouthOpen * headW * 0.15;
  const isPixel = props.style === 'pixel';
  const border = isPixel ? `6px solid ${s.outline}` : `4px solid ${s.outline}`;
  const blink = Math.sin((frame + 7) / 31) > 0.97 ? 0.15 : 1;
  return (
    <AbsoluteFill style={{background: s.bg, overflow: 'hidden', fontFamily: s.font}}>
      <AbsoluteFill style={{opacity: 0.55}}>
        {Array.from({length: 9}, (_, i) => (
          <div
            key={'bg-' + i}
            style={{
              position: 'absolute',
              left: `${(i * 37 + 11) % 100}%`,
              top: `${(i * 19 + 5) % 100}%`,
              width: 4 + (i % 3) * 3,
              height: 4 + (i % 3) * 3,
              background: s.accent,
              opacity: 0.3 + 0.5 * Math.abs(Math.sin((frame + i * 13) / 20)),
              boxShadow: `0 0 12px ${s.accent}`,
            }}
          />
        ))}
      </AbsoluteFill>
      <div
        style={{
          position: 'absolute',
          left: width / 2 - headW / 2,
          top: height * 0.28 + bob,
          width: headW,
          height: headH,
          borderRadius: s.radius,
          background: s.skin,
          border,
          boxShadow: `0 0 ${width * 0.05}px ${s.accent}66`,
          opacity: out,
          transform: `scale(${interpolate(enter, [0, 1], [0.85, 1])})`,
        }}
      >
        <div
          style={{
            position: 'absolute',
            left: '8%',
            right: '8%',
            top: -headH * 0.12,
            height: headH * 0.3,
            background: s.hair,
            borderRadius: isPixel ? 0 : `${s.radius}px ${s.radius}px 20% 20%`,
            border: isPixel ? `6px solid ${s.outline}` : 'none',
          }}
        />
        <div style={{position: 'absolute', left: '22%', top: eyeTop, width: eyeGap, height: eyeGap * (exp === 'happy' ? 0.6 : 1), background: s.outline, borderRadius: isPixel ? 0 : '50%', transform: `scaleY(${blink})`}} />
        <div style={{position: 'absolute', right: '22%', top: eyeTop, width: eyeGap, height: eyeGap * (exp === 'happy' ? 0.6 : 1), background: s.outline, borderRadius: isPixel ? 0 : '50%', transform: `scaleY(${blink})`}} />
        {exp === 'thinking' ? (
          <div style={{position: 'absolute', right: '18%', top: eyeTop + eyeGap * 0.1, width: eyeGap * 0.35, height: eyeGap * 0.35, borderRadius: '50%', background: s.accent, opacity: 0.8 + 0.2 * Math.sin(frame / 6)}} />
        ) : null}
        {exp === 'serious' ? (
          <div style={{position: 'absolute', left: '20%', right: '20%', top: eyeTop - 6, height: 8, background: s.outline, transform: 'rotate(-4deg)'}} />
        ) : null}
        <div
          style={{
            position: 'absolute',
            left: '50%',
            top: headH * 0.62,
            width: mouthW,
            height: mouthH,
            marginLeft: -mouthW / 2,
            background: exp === 'happy' ? '#7a2038' : '#3a0d18',
            border: `3px solid ${s.outline}`,
            borderRadius: exp === 'happy' ? '0 0 999px 999px' : isPixel ? 0 : '999px',
            overflow: 'hidden',
          }}
        >
          {isSpeaking ? <div style={{position: 'absolute', left: '15%', right: '15%', bottom: 0, height: mouthH * 0.35, background: '#ff7b9c'}} /> : null}
        </div>
        <div
          style={{
            position: 'absolute',
            left: -headW * 0.22,
            top: headH * 0.5,
            width: headW * 0.26,
            height: headW * 0.12,
            background: s.skin,
            border,
            borderRadius: isPixel ? 0 : 999,
            transform: `rotate(${exp === 'surprising' ? -28 : -12 + Math.sin(frame / 9) * 6}deg)`,
          }}
        />
      </div>
      <div
        style={{
          position: 'absolute',
          left: 0,
          right: 0,
          bottom: height * 0.12,
          textAlign: 'center',
          color: s.accent,
          fontSize: width * 0.04,
          fontWeight: 800,
          letterSpacing: 3,
          opacity: out,
        }}
      >
        {props.label}
      </div>
      <div
        style={{
          position: 'absolute',
          left: width * 0.25,
          right: width * 0.25,
          bottom: height * 0.09,
          height: 8,
          background: '#ffffff22',
          overflow: 'hidden',
          opacity: out,
        }}
      >
        <div
          style={{
            width: `${Math.min(100, (frame / Math.max(1, durationInFrames)) * 100)}%`,
            height: '100%',
            background: s.accent,
          }}
        />
      </div>
      {isPixel ? (
        <AbsoluteFill
          style={{
            pointerEvents: 'none',
            background: 'repeating-linear-gradient(0deg, rgba(0,0,0,0.18) 0px, rgba(0,0,0,0.18) 2px, transparent 2px, transparent 5px)',
            opacity: 0.35,
          }}
        />
      ) : null}
    </AbsoluteFill>
  );
};
