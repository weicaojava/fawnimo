/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ScriptBeat } from './ScriptBeat';
import type { ScriptDialogue } from './ScriptDialogue';
import type { ShotBinding } from './ShotBinding';
/**
 * Editable incomplete script using integer durations and stable references.
 */
export type ScriptDocument = {
    schema_version?: number;
    title?: string;
    script_text?: string;
    target_duration_ms?: number;
    character_ids?: Array<string>;
    scene_ids?: Array<string>;
    beats?: Array<ScriptBeat>;
    dialogues?: Array<ScriptDialogue>;
    bindings?: Array<ShotBinding>;
};

