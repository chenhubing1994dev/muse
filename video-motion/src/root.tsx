import React from 'react';
import {Composition} from 'remotion';
import {MuseBrandMotion, calculateBrandMotionMetadata, defaultBrandMotionProps} from './scenes/MuseBrandMotion';
import {MuseBumper, calculateMuseBumperMetadata, defaultMuseBumperProps} from './scenes/MuseBumper';
import {MuseSceneTimeline, calculateMuseSceneTimelineMetadata, defaultMuseSceneTimelineProps} from './scenes/MuseSceneTimeline';

export const Root: React.FC = () => (
  <>
    <Composition
      id="MuseBrandMotion"
      component={MuseBrandMotion}
      width={1080}
      height={1920}
      fps={30}
      durationInFrames={204}
      defaultProps={defaultBrandMotionProps}
      calculateMetadata={calculateBrandMotionMetadata}
    />
    <Composition
      id="MuseBumper"
      component={MuseBumper}
      width={1080}
      height={1920}
      fps={30}
      durationInFrames={60}
      defaultProps={defaultMuseBumperProps}
      calculateMetadata={calculateMuseBumperMetadata}
    />
    <Composition
      id="MuseSceneTimeline"
      component={MuseSceneTimeline}
      width={1920}
      height={1080}
      fps={30}
      durationInFrames={60}
      defaultProps={defaultMuseSceneTimelineProps}
      calculateMetadata={calculateMuseSceneTimelineMetadata}
    />
  </>
);
