import React from 'react';
import {AbsoluteFill, Easing, interpolate, spring, useCurrentFrame, useVideoConfig, type CalculateMetadataFunction} from 'remotion';

export type MuseBumperProps = {
  title: string;
  subtitle: string;
  accent: string;
  width: number;
  height: number;
  fps: number;
  durationInFrames: number;
  kind: 'intro' | 'outro';
};

export const defaultMuseBumperProps: MuseBumperProps = {
  title: '让未知成为探索的起点',
  subtitle: 'MUSE · RESEARCH / CREATE / EVOLVE',
  accent: '#d8b46a',
  width: 1080,
  height: 1920,
  fps: 30,
  durationInFrames: 60,
  kind: 'intro',
};

export const calculateMuseBumperMetadata: CalculateMetadataFunction<MuseBumperProps> = ({props}) => {
  const width = Math.max(64, Math.min(3840, Math.round(props.width || 1080)));
  const height = Math.max(64, Math.min(3840, Math.round(props.height || 1920)));
  const fps = Math.max(1, Math.min(60, Math.round(props.fps || 30)));
  const durationInFrames = Math.max(1, Math.min(300, Math.round(props.durationInFrames || fps * 2)));
  return {width, height, fps, durationInFrames};
};

export const MuseBumper: React.FC<MuseBumperProps> = (props) => {
  const frame = useCurrentFrame();
  const {width, height, fps, durationInFrames} = useVideoConfig();
  const entrance = spring({frame, fps, config: {damping: 18, stiffness: 92, mass: 0.8}});
  const fadeOut = interpolate(
    frame,
    [Math.max(0, durationInFrames - Math.round(fps * 0.35)), durationInFrames],
    [1, 0],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.inOut(Easing.quad)},
  );
  const glow = 42 + Math.sin(frame / 17) * 5;
  const orbit = frame * 0.09;
  const title = props.title.trim();
  return (
    <AbsoluteFill style={{
      background: `radial-gradient(circle at ${50 + Math.sin(frame / 31) * 5}% ${glow}%, #263b59 0%, #0a1020 46%, #040710 100%)`,
      color: '#f4f0e8', justifyContent: 'center', alignItems: 'center',
      padding: width * 0.09, textAlign: 'center', opacity: fadeOut,
    }}>
      <div style={{
        position: 'absolute', inset: width * 0.055,
        border: `1px solid ${props.accent}66`,
        transform: `rotate(${orbit}deg) scale(${1 + Math.sin(frame / 23) * 0.012})`,
      }} />
      <div style={{
        position: 'absolute', top: '23%', left: '12%', right: '12%', height: 2,
        background: `linear-gradient(90deg, transparent, ${props.accent}, transparent)`,
        opacity: 0.4 + Math.sin(frame / 9) * 0.15,
      }} />
      <div style={{
        color: props.accent, fontSize: Math.round(width * 0.025),
        letterSpacing: 6, marginBottom: height * 0.035,
        opacity: entrance,
      }}>MUSE / {props.kind === 'intro' ? 'IGNITION' : 'CONTINUUM'}</div>
      <div style={{
        maxWidth: '90%', fontSize: Math.round(width * 0.075), fontWeight: 750,
        lineHeight: 1.28, transform: `translateY(${(1 - entrance) * 42}px) scale(${interpolate(entrance, [0, 1], [1.08, 1])})`,
        textShadow: `0 0 38px ${props.accent}33`,
      }}>{title}</div>
      <div style={{
        width: width * 0.2, height: 3, background: props.accent,
        marginTop: height * 0.04, transform: `scaleX(${entrance})`,
      }} />
      <div style={{
        color: '#b6c2d4', fontSize: Math.round(width * 0.021),
        letterSpacing: 3, marginTop: height * 0.03, opacity: entrance,
      }}>{props.subtitle}</div>
      <div style={{
        position: 'absolute', bottom: height * 0.06,
        color: '#8290a7', fontSize: Math.round(width * 0.016), letterSpacing: 3,
      }}>MUSE · LOCAL CREATIVE ENGINE</div>
    </AbsoluteFill>
  );
};
