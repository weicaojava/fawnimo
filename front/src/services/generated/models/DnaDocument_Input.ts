/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DnaAsset_Input } from './DnaAsset_Input';
import type { DnaClaim } from './DnaClaim';
import type { DnaShot_Input } from './DnaShot_Input';
/**
 * An editable bilingual bible and whole-source shot set, without execution settings.
 */
export type DnaDocument_Input = {
    schema_version?: number;
    title?: string;
    reviewer?: string;
    human_reviewed?: boolean;
    global_style?: DnaClaim;
    negative_constraints?: DnaClaim;
    audio_editing?: DnaClaim;
    assets?: Array<DnaAsset_Input>;
    shots?: Array<DnaShot_Input>;
};

