/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { TransitionDocument } from './TransitionDocument';
/**
 * Persist a complete boundary form under compare-and-swap revision control.
 */
export type TransitionPatch = {
    expected_revision: number;
    document: TransitionDocument;
};

