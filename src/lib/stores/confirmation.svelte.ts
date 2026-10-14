import { createContext } from "svelte";

type ConfirmationOptions = {
  title: string;
  description: string;
  confirmText?: string;
  cancelText?: string;
  variant?: "default" | "destructive";
};

type ConfirmationState = {
  isOpen: boolean;
  options: ConfirmationOptions | null;
  resolve: ((isConfirmed: boolean) => void) | null;
};

export class ConfirmationStore {
  private state = $state<ConfirmationState>({
    isOpen: false,
    options: null,
    resolve: null,
  });

  private close() {
    this.state.isOpen = false;
    setTimeout(() => {
      this.state.options = null;
      this.state.resolve = null;
    }, 300);
  }

  get isOpen() {
    return this.state.isOpen;
  }

  get options() {
    return this.state.options;
  }

  async confirm(options: ConfirmationOptions): Promise<boolean> {
    return new Promise<boolean>((resolve) => {
      this.state = {
        isOpen: true,
        options: {
          confirmText: "Continue",
          cancelText: "Cancel",
          variant: "default",
          ...options,
        },
        resolve,
      };
    });
  }

  handleConfirm() {
    if (this.state.resolve) {
      this.state.resolve(true);
    }
    this.close();
  }

  handleCancel() {
    if (this.state.resolve) {
      this.state.resolve(false);
    }
    this.close();
  }
}

export const [getConfirmationContext, setConfirmationContext] =
  createContext<ConfirmationStore>();
