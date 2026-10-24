/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { BenchmarkEvent } from './BenchmarkEvent';
import type { Dialogue } from './Dialogue';
import type { MouthAnchor } from './MouthAnchor';
import type { Role } from './Role';
import type { RubricNotes } from './RubricNotes';
/**
 * Incomplete drafts are allowed; semantic claims remain human declarations.
 */
export type BenchmarkDocument = {
    schema_version?: number;
    title?: string;
    source_note?: string;
    license_basis?: 'owned' | 'permission' | 'open_license' | 'unknown';
    license_reference?: string;
    license_confirmed?: boolean;
    independent_from_target?: boolean;
    continuous_take?: boolean;
    reviewer?: string;
    human_reviewed?: boolean;
    roles?: Array<Role>;
    dialogues?: Array<Dialogue>;
    mouth_anchors?: Array<MouthAnchor>;
    events?: Array<BenchmarkEvent>;
    cut_frame_indices?: Array<number>;
    rubric_notes?: RubricNotes;
};

