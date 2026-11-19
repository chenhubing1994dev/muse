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

export type SubtitleStyle = 'hk-neon' | 'classic' | 'holo';

export type NarrationSubtitleSegment = {
  startFrame: number;
  endFrame: number;
  text: string;
};

export type NarrationSubtitleProps = {
  segments: NarrationSubtitleSegment[];
  style: SubtitleStyle;
  width: number;
  height: number;
  fps: number;
  durationInFrames: number;
};

type SubtitleVisual = {
  color: string;
  stroke: string;
  strokeWidth: number;
  shadow: string;
  background: string;
  border: string;
};

const VISUALS: Record<SubtitleStyle, SubtitleVisual> = {
  'hk-neon': {
    color: '#ffe600',
    stroke: '#ffffff',
    strokeWidth: 6,
    shadow: '0 0 24px #ff2d95, 3px 3px 0 #00000099',
    background: 'rgba(10, 4, 14, 0.45)',
    border: '2px solid #ff2d9555',
  },
  classic: {
    color: '#ffffff',
    stroke: '#000000',
    strokeWidth: 5,
    shadow: '0 2px 6px #000000cc',
    background: 'rgba(0, 0, 0, 0.55)',
    border: '1px solid #ffffff33',
  },
  holo: {
    color: '#d6fbff',
    stroke: '#0af',
    strokeWidth: 4,
    shadow: '0 0 18px #00e5ff, 0 0 36px #00e5ff66',
    background: 'linear-gradient(90deg, rgba(0,229,255,0.08), rgba(6,16,38,0.7), rgba(0,229,255,0.08))',
    border: '2px solid #00e5ff88',
  },
};

export const defaultNarrationSubtitleProps: NarrationSubtitleProps = {
  segments: [
    {startFrame: 0, endFrame: 60, text: '每一段灵感，都值得被认真对待。'},
    {startFrame: 60, endFrame: 120, text: '从研究到创造，让未知成为起点。'},
    {startFrame: 120, endFrame: 180, text: 'MUSE · 把知识之火传递下去。'},
  ],
  style: 'hk-neon',
  width: 1080,
  height: 1920,
  fps: 30,
  durationInFrames: 180,
};

export const calculateNarrationSubtitleMetadata: CalculateMetadataFunction<NarrationSubtitleProps> = ({props}) => {
  const width = Math.max(64, Math.min(3840, Math.round(props.width || 1080)));
  const height = Math.max(64, Math.min(3840, Math.round(props.height || 1920)));
  const fps = Math.max(24, Math.min(60, Math.round(props.fps || 30)));
  const segments = Array.isArray(props.segments) ? props.segments : [];
  const derived = segments.length ? Math.max(...segments.map((seg) => Math.round(seg.endFrame))) : 180;
  const durationInFrames = Math.max(30, Math.min(3600, Math.round(props.durationInFrames || derived)));
  return {width, height, fps, durationInFrames};
};

export const NarrationSubtitle: React.FC<NarrationSubtitleProps> = (props) => {
  const frame = useCurrentFrame();
  const {width, height, fps, durationInFrames} = useVideoConfig();
  const visual = VISUALS[props.style] ?? VISUALS['hk-neon'];
  const segments = Array.isArray(props.segments) ? props.segments : [];
  const activeIndex = segments.findIndex((seg) => frame >= seg.startFrame && frame < seg.endFrame);
  const active = activeIndex >= 0 ? segments[activeIndex] : null;
  const localFrame = active ? frame - active.startFrame : 0;
  const enter = spring({frame: localFrame, fps, config: {damping: 20, stiffness: 130, mass: 0.7}});
  const fadeOut = active
    ? interpolate(frame, [active.endFrame - 8, active.endFrame], [1, 0], {
        extrapolateLeft: 'clamp',
        extrapolateRight: 'clamp',
        easing: Easing.inOut(Easing.quad),
      })
    : 0;
  const chars = active ? Array.from(active.text) : [];
  const fontSize = Math.round(width * 0.052);
  return (
    <AbsoluteFill style={{pointerEvents: 'none', fontFamily: '"Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", sans-serif'}}>
      <div
        style={{
          position: 'absolute',
          left: width * 0.07,
          right: width * 0.07,
          bottom: height * 0.1,
          display: 'flex',
          justifyContent: 'center',
          alignItems: 'flex-end',
        }}
      >
        {active ? (
          <div
            style={{
              padding: `${width * 0.026}px ${width * 0.04}px`,
              background: visual.background,
              border: visual.border,
              borderRadius: props.style === 'holo' ? 4 : 16,
              boxShadow: '0 12px 34px rgba(0,0,0,0.55)',
              textAlign: 'center',
              maxWidth: '100%',
              opacity: fadeOut,
              transform: `translateY(${(1 - enter) * 22}px) scale(${interpolate(enter, [0, 1], [0.94, 1])})`,
            }}
          >
            <span style={{fontSize, fontWeight: 900, lineHeight: 1.38, whiteSpace: 'pre-wrap'}}>
              {chars.map((ch, i) => {
                const highlight = interpolate(localFrame - i * 1.5, [0, 4], [0.6, 1], {
                  extrapolateLeft: 'clamp',
                  extrapolateRight: 'clamp',
                });
                return (
                  <span
                    key={'ch-' + i}
                    style={{
                      color: visual.color,
                      WebkitTextStroke: `${visual.strokeWidth}px ${visual.stroke}`,
                      textShadow: visual.shadow,
                      opacity: highlight,
                    }}
                  >
                    {ch}
                  </span>
                );
              })}
            </span>
            {props.style === 'holo' ? (
              <div
                style={{
                  position: 'absolute',
                  left: 0,
                  right: 0,
                  top: 0,
                  height: 3,
                  background: 'linear-gradient(90deg, transparent, #00e5ff, transparent)',
                  opacity: 0.8 + 0.2 * Math.sin(frame / 5),
                }}
              />
            ) : null}
          </div>
        ) : null}
      </div>
      <div
        style={{
          position: 'absolute',
          left: width * 0.12,
          right: width * 0.12,
          bottom: height * 0.075,
          display: 'flex',
          gap: 6,
          justifyContent: 'center',
        }}
      >
        {segments.map((seg, i) => {
          const isActive = i === activeIndex;
          const isPast = frame >= seg.endFrame;
          return (
            <div
              key={'seg-' + i}
              style={{
                width: isActive ? width * 0.06 : width * 0.022,
                height: 4,
                borderRadius: 999,
                background: isActive ? visual.color : isPast ? `${visual.color}88` : '#ffffff33',
              }}
            />
          );
        })}
      </div>
    </AbsoluteFill>
  );
};
