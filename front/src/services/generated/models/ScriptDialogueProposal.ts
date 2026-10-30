/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Bounded original spoken text with optional named speakers for later confirmation.
 */
export type ScriptDialogueProposal = {
    text: string;
    speaker_name?: (string | null);
    target_name?: (string | null);
    line_mode?: 'DIALOGUE' | 'VOICE_OVER' | 'OFF_SCREEN' | 'PHONE';
};

