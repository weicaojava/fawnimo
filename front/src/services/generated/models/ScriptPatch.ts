/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ScriptDocument } from './ScriptDocument';
/**
 * Replace a draft under optimistic revision control.
 */
export type ScriptPatch = {
    expected_revision: number;
    document: ScriptDocument;
};

