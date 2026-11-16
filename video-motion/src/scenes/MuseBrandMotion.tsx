import React from 'react';
import {AbsoluteFill, Easing, interpolate, spring, useCurrentFrame, useVideoConfig, type CalculateMetadataFunction} from 'remotion';
import {TransitionSeries, linearTiming} from '@remotion/transitions';
import {fade} from '@remotion/transitions/fade';
import {wipe} from '@remotion/transitions/wipe';

type Props = {title: string; subtitle: string; accent: string; aspectRatio: '9:16' | '16:9' | '1:1'; fps: number};
export const defaultBrandMotionProps: Props = {
  title: '让未知成为探索的起点',
  subtitle: 'MUSE · RESEARCH / CREATE / EVOLVE',
  accent: '#d8b46a',
  aspectRatio: '9:16',
  fps: 30,
};

export const calculateBrandMotionMetadata: CalculateMetadataFunction<Props> = ({props}) => {
  const dimensions = props.aspectRatio === '16:9' ? {width: 1920, height: 1080} : props.aspectRatio === '1:1' ? {width: 1080, height: 1080} : {width: 1080, height: 1920};
  return {...dimensions, fps: Math.max(24, Math.min(60, Math.round(props.fps || 30))), durationInFrames: 204};
};

type SceneProps = {title: string; subtitle: string; accent: string; chapter: string; variant: number; durationInFrames: number; fadeOutFrames: number};
const Scene: React.FC<SceneProps> = ({title, subtitle, accent, chapter, variant, durationInFrames, fadeOutFrames}) => {
  const frame = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();
  const intro = spring({frame, fps, config: {damping: 18, stiffness: 90, mass: 0.8}});
  const opacity = interpolate(frame, [0, 10, durationInFrames - fadeOutFrames, durationInFrames], [0, 1, 1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.inOut(Easing.quad)});
  const scale = interpolate(intro, [0, 1], [1.08, 1]);
  const glowX = 50 + Math.sin(frame / 43) * 4;
  const glowY = 45 + Math.cos(frame / 51) * 4;
  const orbit = frame * 0.055;
  const shimmer = 0.22 + (Math.sin(frame / 18) + 1) * 0.07;
  const background = variant === 0
    ? `radial-gradient(circle at ${glowX}% ${glowY}%, #20375a 0%, #0a1020 48%, #050812 100%)`
    : `radial-gradient(circle at ${glowX}% ${glowY}%, #342b22 0%, #111827 45%, #050812 100%)`;
  return <AbsoluteFill style={{background, color: '#f4f0e8', justifyContent: 'center', alignItems: 'center', padding: width * 0.09, textAlign: 'center'}}>
    <div style={{position: 'absolute', inset: width * 0.06, border: `1px solid ${accent}55`, opacity: 0.6, transform: `rotate(${orbit}deg) scale(${1 + Math.sin(frame / 61) * 0.012})`}} />
    <div style={{position: 'absolute', left: '12%', right: '12%', top: `${22 + Math.sin(frame / 27) * 1.5}%`, height: 1, background: `linear-gradient(90deg, transparent, ${accent}, transparent)`, opacity: shimmer}} />
    <div style={{fontSize: Math.round(width * 0.018), letterSpacing: 5, color: accent, marginBottom: height * 0.035, opacity}}>{chapter}</div>
    <div style={{fontSize: Math.round(width * 0.07), fontWeight: 700, lineHeight: 1.25, maxWidth: '88%', transform: `translateY(${(1-intro)*34}px) scale(${scale})`, opacity}}>{title}</div>
    <div style={{width: width * 0.18, height: 2, background: accent, marginTop: height * 0.035, transform: `scaleX(${intro})`}} />
    <div style={{fontSize: Math.round(width * 0.019), letterSpacing: 3, color: '#aab6c8', marginTop: height * 0.025, opacity}}>{subtitle}</div>
    <div style={{position: 'absolute', bottom: height * 0.055, fontSize: Math.round(width * 0.014), color: '#74839a', letterSpacing: 2, opacity: 0.8 + Math.sin(frame / 20) * 0.08}}>MUSE · LOCAL CREATIVE ENGINE</div>
  </AbsoluteFill>;
};

export const MuseBrandMotion: React.FC<Props> = (props) => (
  <TransitionSeries>
    <TransitionSeries.Sequence durationInFrames={105}>
      <Scene title={props.title} subtitle={props.subtitle} accent={props.accent} chapter="MUSE / IGNITION" variant={0} durationInFrames={105} fadeOutFrames={24} />
    </TransitionSeries.Sequence>
    <TransitionSeries.Transition presentation={fade()} timing={linearTiming({durationInFrames: 24})} />
    <TransitionSeries.Sequence durationInFrames={99}>
      <Scene title="研究 · 创造 · 进化" subtitle="FROM UNKNOWN TO UNDERSTANDING" accent={props.accent} chapter="THE LIVING KNOWLEDGE" variant={1} durationInFrames={99} fadeOutFrames={24} />
    </TransitionSeries.Sequence>
    <TransitionSeries.Transition presentation={wipe()} timing={linearTiming({durationInFrames: 24})} />
    <TransitionSeries.Sequence durationInFrames={48}>
      <Scene title="让灵感，成为作品。" subtitle="MUSE" accent={props.accent} chapter="CREATE WHAT MATTERS" variant={0} durationInFrames={48} fadeOutFrames={12} />
    </TransitionSeries.Sequence>
  </TransitionSeries>
);