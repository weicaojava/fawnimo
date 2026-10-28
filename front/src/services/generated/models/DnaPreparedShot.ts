/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DnaIssue } from './DnaIssue';
import type { DnaPreparedLayer } from './DnaPreparedLayer';
import type { DnaPreparedUnknown } from './DnaPreparedUnknown';
/**
 * Frame-based reconstruction preparation; the terminal duration is deliberately unknown.
 */
export type DnaPreparedShot = {
    shot_id: string;
    start_frame: number;
    end_frame: number;
    start_relative_us: number;
    end_relative_us: (number | null);
    character_ids: Array<string>;
    scene_id: (string | null);
    prop_ids: Array<string>;
    layers: Array<DnaPreparedLayer>;
    unknowns: Array<DnaPreparedUnknown>;
    issues: Array<DnaIssue>;
};

