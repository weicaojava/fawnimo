/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DnaClaim } from './DnaClaim';
import type { DnaDialogue_Output } from './DnaDialogue_Output';
import type { DnaEvent_Output } from './DnaEvent_Output';
/**
 * A half-open original frame interval and its editable reconstruction claims.
 */
export type DnaShot_Output = {
    id: string;
    start_frame: number;
    end_frame: number;
    character_ids?: Array<string>;
    scene_id?: (string | null);
    prop_ids?: Array<string>;
    composition?: DnaClaim;
    camera?: DnaClaim;
    lighting?: DnaClaim;
    action?: DnaClaim;
    continuity?: DnaClaim;
    audio?: DnaClaim;
    image_prompt?: DnaClaim;
    video_prompt?: DnaClaim;
    dialogues?: Array<DnaDialogue_Output>;
    events?: Array<DnaEvent_Output>;
};

