import React from 'react';
import {
  AbsoluteFill,
  OffthreadVideo,
  interpolate,
  staticFile,
  useCurrentFrame,
  type CalculateMetadataFunction,
} from 'remotion';
import {TransitionSeries, linearTiming} from '@remotion/transitions';
import {fade} from '@remotion/transitions/fade';
import {wipe} from '@remotion/transitions/wipe';

type CameraMove = 'none' | 'push_in' | 'pull_out' | 'drift_left' | 'drift_right' | 'rise';
type VfxPreset = 'none' | 'embers' | 'mist' | 'moonlight' | 'ink';
type Scene = {
  src: string;
  durationInFrames: number;
  cameraMove?: CameraMove;
  vfx?: VfxPreset;
  accent?: string;
};
type Props = {
  scenes: Scene[];
  width: number;
  height: number;
  fps: number;
  transitionDurationInFrames: number;
};

export const defaultMuseSceneTimelineProps: Props = {
  scenes: [{src: 'muse-scene-placeholder.mp4', durationInFrames: 60}],
  width: 1920,
  height: 1080,
  fps: 30,
  transitionDurationInFrames: 12,
};

export const calculateMuseSceneTimelineMetadata: CalculateMetadataFunction<Props> = ({props}) => {
  const transitionCount = Math.max(0, props.scenes.length - 1);
  const sourceFrames = props.scenes.reduce((sum, scene) => sum + scene.durationInFrames, 0);
  const durationInFrames = Math.max(1, sourceFrames - transitionCount * props.transitionDurationInFrames);
  return {
    width: props.width,
    height: props.height,
    fps: props.fps,
    durationInFrames,
  };
};

const SceneVisual: React.FC<{scene: Scene}> = ({scene}) => {
  const frame = useCurrentFrame();
  const endFrame = Math.max(1, scene.durationInFrames - 1);
  const progress = interpolate(frame, [0, endFrame], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const move = scene.cameraMove ?? 'none';
  let zoom = 1;
  let panX = 0;
  let panY = 0;
  if (move === 'push_in') zoom = 1 + 0.12 * progress;
  if (move === 'pull_out') zoom = 1.12 - 0.12 * progress;
  if (move === 'drift_left') { zoom = 1.08; panX = 2 - 4 * progress; }
  if (move === 'drift_right') { zoom = 1.08; panX = -2 + 4 * progress; }
  if (move === 'rise') { zoom = 1.08; panY = 2 - 4 * progress; }

  const vfx = scene.vfx ?? 'none';
  const accent = scene.accent ?? '#d8b46a';
  return (
    <AbsoluteFill style={{overflow: 'hidden', backgroundColor: '#050812'}}>
      <OffthreadVideo
        src={staticFile(scene.src)}
        muted
        style={{
          width: '100%',
          height: '100%',
          objectFit: 'contain',
          transformOrigin: '50% 50%',
          transform: 'translate3d(' + panX + '%, ' + panY + '%, 0) scale(' + zoom + ')',
        }}
      />
      {vfx === 'moonlight' ? (
        <AbsoluteFill style={{
          pointerEvents: 'none',
          background: 'radial-gradient(ellipse at 12% 8%, rgba(150,190,255,0.30), transparent 52%), linear-gradient(118deg, rgba(140,180,255,0.13), transparent 38%)',
          mixBlendMode: 'screen',
        }} />
      ) : null}
      {vfx === 'mist' ? (
        <AbsoluteFill style={{
          pointerEvents: 'none',
          background: 'radial-gradient(ellipse at 18% 78%, rgba(185,205,225,0.25), transparent 55%), radial-gradient(ellipse at 78% 62%, rgba(150,175,205,0.17), transparent 48%)',
          opacity: 0.72 + 0.12 * Math.sin(frame / 18),
          mixBlendMode: 'screen',
        }} />
      ) : null}
      {vfx === 'ink' ? (
        <AbsoluteFill style={{
          pointerEvents: 'none',
          background: 'radial-gradient(ellipse at center, transparent 35%, rgba(3,7,18,0.68) 100%)',
        }} />
      ) : null}
      {vfx === 'embers' ? (
        <AbsoluteFill style={{pointerEvents: 'none', overflow: 'hidden'}}>
          {Array.from({length: 18}, (_, index) => {
            const drift = ((frame * (0.32 + (index % 5) * 0.055) + index * 23) % 120) / 120;
            const left = (index * 47 + 11) % 100;
            const size = 2 + (index % 4) * 1.3;
            const opacity = 0.18 + 0.5 * Math.abs(Math.sin((frame + index * 13) / 17));
            return (
              <div
                key={'ember-' + index}
                style={{
                  position: 'absolute',
                  left: left + '%',
                  top: (100 - drift * 118) + '%',
                  width: size,
                  height: size,
                  borderRadius: '50%',
                  backgroundColor: accent,
                  boxShadow: '0 0 12px ' + accent,
                  opacity,
                }}
              />
            );
          })}
          <AbsoluteFill style={{
            background: 'linear-gradient(0deg, rgba(80,25,8,0.12), transparent 46%)',
            mixBlendMode: 'screen',
          }} />
        </AbsoluteFill>
      ) : null}
    </AbsoluteFill>
  );
};

export const MuseSceneTimeline: React.FC<Props> = ({scenes, transitionDurationInFrames}) => {
  const children: React.ReactNode[] = [];
  scenes.forEach((scene, index) => {
    children.push(
      <TransitionSeries.Sequence key={'scene-' + index} durationInFrames={scene.durationInFrames}>
        <SceneVisual scene={scene} />
      </TransitionSeries.Sequence>,
    );
    if (index < scenes.length - 1) {
      const timing = linearTiming({durationInFrames: transitionDurationInFrames});
      if (index % 2 === 0) {
        children.push(
          <TransitionSeries.Transition
            key={'transition-' + index}
            presentation={fade()}
            timing={timing}
          />,
        );
      } else {
        children.push(
          <TransitionSeries.Transition
            key={'transition-' + index}
            presentation={wipe({direction: 'from-left'})}
            timing={timing}
          />,
        );
      }
    }
  });
  return <TransitionSeries>{children}</TransitionSeries>;
};
