/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ScriptShotProposal } from './ScriptShotProposal';
/**
 * A finite proposed division cannot delete or overwrite existing shots.
 */
export type ScriptDivisionProposal = {
    shots: Array<ScriptShotProposal>;
    summary?: string;
};

