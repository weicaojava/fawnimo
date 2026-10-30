/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ScriptDialogueProposal } from './ScriptDialogueProposal';
import type { ScriptEntityProposal } from './ScriptEntityProposal';
/**
 * Extract only names, descriptions and dialogue; it cannot link actual foreign assets.
 */
export type ScriptExtractionProposal = {
    characters?: Array<ScriptEntityProposal>;
    scenes?: Array<ScriptEntityProposal>;
    props?: Array<ScriptEntityProposal>;
    costumes?: Array<ScriptEntityProposal>;
    dialogues?: Array<ScriptDialogueProposal>;
    summary?: string;
};

