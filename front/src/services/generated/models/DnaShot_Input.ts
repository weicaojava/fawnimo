/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DnaClaim } from './DnaClaim';
import type { DnaDialogue_Input } from './DnaDialogue_Input';
import type { DnaEvent_Input } from './DnaEvent_Input';
/**
 * A half-open original frame interval and its editable reconstruction claims.
 */
export type DnaShot_Input = {
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
    dialogues?: Array<DnaDialogue_Input>;
    events?: Array<DnaEvent_Input>;
};

