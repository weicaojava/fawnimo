/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { EditDocument } from './EditDocument';
import type { SoundTrack } from './SoundTrack';
import type { TransitionEdge } from './TransitionEdge';
/**
 * Own the complete picture and sound form independently of mutable v1/v2 drafts.
 */
export type SoundDocument_Output = {
    schema_version?: number;
    title?: string;
    picture: EditDocument;
    edges?: Array<TransitionEdge>;
    tracks?: Array<SoundTrack>;
};

