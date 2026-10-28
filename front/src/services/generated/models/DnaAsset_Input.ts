/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DnaClaim } from './DnaClaim';
/**
 * A local analysis identity optionally mapped by a human to one owned production version.
 */
export type DnaAsset_Input = {
    id: string;
    kind: 'character' | 'scene' | 'prop';
    name_zh?: string;
    name_en?: string;
    fields?: Record<string, DnaClaim>;
    lock_prompt?: DnaClaim;
    production_version_id?: (string | null);
    binding_note?: string;
};

