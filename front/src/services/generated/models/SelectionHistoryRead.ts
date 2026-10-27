/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { EditTakeOption } from './EditTakeOption';
import type { SelectionRead } from './SelectionRead';
/**
 * Selection audit and immutable takes, including legacy history.
 */
export type SelectionHistoryRead = {
    shot_id: string;
    current_take_id: (string | null);
    revision: number;
    events: Array<SelectionRead>;
    takes: Array<EditTakeOption>;
};

