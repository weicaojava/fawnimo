/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DnaAsset_Output } from './DnaAsset_Output';
import type { DnaClaim } from './DnaClaim';
import type { DnaShot_Output } from './DnaShot_Output';
/**
 * An editable bilingual bible and whole-source shot set, without execution settings.
 */
export type DnaDocument_Output = {
    schema_version?: number;
    title?: string;
    reviewer?: string;
    human_reviewed?: boolean;
    global_style?: DnaClaim;
    negative_constraints?: DnaClaim;
    audio_editing?: DnaClaim;
    assets?: Array<DnaAsset_Output>;
    shots?: Array<DnaShot_Output>;
};

